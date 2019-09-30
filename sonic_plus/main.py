#! /usr/bin/python -u

import os
import stat
import json
import click
import shlex
import docker
import subprocess
from jinja2 import Template
from tabulate import tabulate
from natsort import natsorted

VARLIB_PATH = "/var/lib/sonic-plus/"
REPO_PATH = VARLIB_PATH + "repo"
SCHEMA_PATH = "/schema.json"
INSTALL_PATH = "/usr/lib/python2.7/dist-packages/"
CLI_TEMPLATE_PATH = "/var/lib/sonic-plus/sonic-plus-cli.j2"
ENTRY_POINT_TEMPLATE_PATH = "/var/lib/sonic-plus/sonic-plus-entry-point.j2"
BASH_COMPLETION_TEMPLATE_PATH = "/var/lib/sonic-plus/sonic-plus-bash-completion.j2"

REPO_HEADER = ('NAME', 'REPO', 'DESCRIPTION', "STATUS")

def get_repo(abort_on_empty=True):
    repo = dict()
    if os.path.isfile(REPO_PATH):
        with open(REPO_PATH) as repo_json:
            repo = json.load(repo_json)
    elif abort_on_empty:
        click.echo("SONiC+ repository list is empty. Try adding some using 'sonic-plus newrepo'")
        raise click.Abort()

    return repo


@click.group()
def cli():
    """ SONiC+ """


@cli.command()
def show():
    """ Show SONiC+ available, installed services """

    repo = get_repo()
    repo_lines = []
    for entry in repo:
        repo_lines.append([
            entry,
            repo[entry]['repo'],
            repo[entry]['description'],
            repo[entry]['status'] if 'status' in repo[entry] else 'Not installed']
            )

    click.echo(tabulate(repo_lines, REPO_HEADER))


@cli.command()
@click.argument('name')
def install(name):
    """ Install SONiC+ service """
    repo = get_repo()

    if name not in repo:
        click.echo("SONiC+ service '%s' not found" % name)
        raise click.Abort()

    if 'status' in repo[name] and repo[name]['status'] == "Installed":
        click.echo("SONiC+ service '%s' is already installed" % name)
        raise click.Abort()

    # TODO: check if installed
    docker_client = docker.from_env()
    docker_api_client = docker.APIClient(base_url='unix://var/run/docker.sock')

    # TODO: Image versioning
    try:
        with click.progressbar(length=100, label='Downloading Docker image... ') as bar:
            for line in docker_api_client.pull(repo[name]['repo'], tag = 'latest', stream=True, decode=True):
                if "progressDetail" in line:
                    if "current" in line['progressDetail']:
                        bar.update(int(line['progressDetail']['current']) * 100 / int(line['progressDetail']['total']))
    except:
        click.echo("Cannot pull docker image, please check the repository availability")
        raise click.Abort()

    try:
        output = docker_client.containers.run(repo[name]['repo'], " %s" % SCHEMA_PATH, remove = True, entrypoint = '/bin/cat')
    except:
        click.echo("Cannot locate a CONFIG_DB schema file. Is it a SONiC+ docker image?")
        raise click.Abort()

    # TODO: schema validation
    schema = json.loads(output)

    click.echo("Generating CLI...")
    package_path = INSTALL_PATH + "/" + name
    os.makedirs(package_path)
    with open(CLI_TEMPLATE_PATH, 'r') as cli_tm_file:
        cli_tm_str = cli_tm_file.read()
    cli_tm = Template(cli_tm_str)
    cli_str = cli_tm.render(schema = schema)
    with open(package_path + "/main.py", 'w') as o:
        o.write(cli_str)
    with open(package_path + "/__init__.py", 'w') as o:
        o.write("")
    with open(ENTRY_POINT_TEMPLATE_PATH, 'r') as entry_point_tm_file:
        entry_point_tm_str = entry_point_tm_file.read()
    entry_point_tm = Template(entry_point_tm_str)
    entry_point_str = entry_point_tm.render(schema = schema)
    with open("/usr/bin/" + name, 'w') as o:
        o.write(entry_point_str)
    os.chmod("/usr/bin/" + name, stat.S_IEXEC | stat.S_IEXEC | stat.S_IEXEC)
    with open(BASH_COMPLETION_TEMPLATE_PATH, 'r') as bash_completion_tm_file:
        bash_completion_tm_str = bash_completion_tm_file.read()
    bash_completion_tm = Template(bash_completion_tm_str)
    bash_completion_str = bash_completion_tm.render(schema = schema)
    with open("/etc/bash_completion.d/" + name, 'w') as o:
        o.write(bash_completion_str)

    try:
        output = docker_client.containers.run(repo[name]['repo'], remove = True, detach = True, tty = True, name = name)
    except:
        click.echo("Cannot run a Docker image")
        raise click.Abort()

    repo[name]['status'] = "Installed"
    with open(REPO_PATH, 'w') as repo_file:
        json.dump(repo, repo_file)

    click.echo("Done.\n\nRun '. /usr/share/bash-completion/bash_completion' to update CLI completion for your bash session.")


@cli.command()
@click.argument('name')
def remove(name):
    """ Remove SONiC+ service """
    repo = get_repo()

    if name not in repo:
        click.echo("SONiC+ service '%s' not found" % name)
        raise click.Abort()

    if 'status' in repo[name] and repo[name]['status'] != "Installed":
        click.echo("SONiC+ service '%s' is not installed" % name)
        raise click.Abort()

    click.echo("removing CLI...")
    package_path = INSTALL_PATH + "/" + name
    os.remove(package_path + "/main.py")
    if os.path.isfile(package_path + "/main.pyc"):
        os.remove(package_path + "/main.pyc")
    os.remove(package_path + "/__init__.py")
    if os.path.isfile(package_path + "/__init__.pyc"):
        os.remove(package_path + "/__init__.pyc")
    os.remove("/usr/bin/" + name)
    os.rmdir(package_path)
    os.remove("/etc/bash_completion.d/" + name)

    # TODO: check if installed
    docker_client = docker.from_env()

    try:
        output = docker_client.containers.get(name).remove(force = True)
    except:
        click.echo("Cannot remove a Docker image")
        raise click.Abort()

    # TODO: Image versioning
    click.echo("removing Docker image...")
    try:
        docker_client.images.remove(repo[name]['repo'])
    except:
        click.echo("Cannot remove docker image")
        raise click.Abort()

    repo[name]['status'] = "Not installed"
    with open(REPO_PATH, 'w') as repo_file:
        json.dump(repo, repo_file)

    click.echo("Done.")


@cli.command()
@click.argument('name')
@click.argument('repo')
def addrepo(name, repo):
    """ Add SONiC+ service repository """
    repo_dict = get_repo(abort_on_empty=False)

    if repo in repo_dict:
        click.echo("Repository already exists")
        raise click.Abort()

    metadata = dict()
    metadata['repo'] = repo
    metadata['description'] = repo
    repo_dict[name] = metadata

    click.echo("Repos")
    click.echo(repo_dict)

    with open(REPO_PATH, 'w') as repo_file:
        json.dump(repo_dict, repo_file)


if __name__ == '__main__':
    cli()
