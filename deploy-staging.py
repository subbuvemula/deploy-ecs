#!/usr/bin/env python3
import subprocess
import sys
import os.path
import json
import re
import argparse
import tempfile

_root_dir = os.path.abspath(os.path.normpath(os.path.dirname(__file__)))
sys.path.insert(0, _root_dir)
from _common import *


def _run_ecs_command(args):
    run_command(['aws', 'ecs', ] + args)


def _get_ecs_output(args):
    return json.loads(run_command(['aws', 'ecs', ] + args, return_stdout=True))


def _tag_image(tag, qualified_image_name):
    log_info('Tagging image \'{}\' as \'{}\'...'.format(
        qualified_image_name, tag))
    log_info('Pulling image from registry in order to tag...')
    run_command(['docker', 'pull', qualified_image_name])
    run_command(['docker', 'tag', '-f', qualified_image_name, '{}:{}'.format(
        qualified_image_name, tag), ])
    log_info('Pushing image tag to registry...')
    run_command(['docker', 'push', '{}:{}'.format(
        qualified_image_name, tag), ])
    log_info('Deleting pulled image...')
    run_command(['docker', 'rmi', '{}:latest'.format(qualified_image_name), ])
    run_command(['docker', 'rmi', '{}:{}'.format(qualified_image_name, tag), ])


def _register_task_definition(task_definition_fpath):
    with open(task_definition_fpath, 'rt') as f:
        task_definition = json.loads(f.read())

    task_family = task_definition['family']

    log_info('Finding existing task definitions of family \'{}\'...'.format(
        task_family
    ))
    existing_task_definitions = _get_ecs_output(['list-task-definitions', ])[
        'taskDefinitionArns']
    for existing_task_definition in [
        td for td in existing_task_definitions if re.match(
            r'arn:aws:ecs+:[^:]+:[^:]+:task-definition/{}:\d+'.format(
                task_family),
            td)]:
        log_info('Deregistering task definition \'{}\'...'.format(
            existing_task_definition))
        _run_ecs_command([
            'deregister-task-definition', '--task-definition',
            existing_task_definition, ])

    tag = run_command([
        'git', 'rev-parse', '--short', 'HEAD', ], return_stdout=True).strip()
    for container_def in task_definition['containerDefinitions']:
        image_name = container_def['image']
        _tag_image(tag, image_name)
        container_def['image'] = '{}:{}'.format(image_name, tag)

    with tempfile.NamedTemporaryFile(mode='wt', suffix='.json') as f:
        task_def_str = json.dumps(task_definition)
        f.write(task_def_str)
        f.flush()
        log_info('Registering task definition...')
        result = _get_ecs_output([
            'register-task-definition',
            '--cli-input-json', 'file://{}'.format(f.name),
        ])

    return '{}:{}'.format(task_family, result['taskDefinition']['revision'])


def _update_service(service_fpath, task_def_name):
    with open(service_fpath, 'rt') as f:
        service_config = json.loads(f.read())
    services = _get_ecs_output(['list-services', ])[
        'serviceArns']
    for service in [s for s in services if re.match(
        r'arn:aws:ecs:[^:]+:[^:]+:service/{}'.format(
            service_config['serviceName']),
        s
    )]:
        log_info('Scaling down service \'{}\'...'.format(service))
        _run_ecs_command([
            'update-service', '--service', service, '--desired-count', '0', ])
        log_info('Scaling service back up...')
        _run_ecs_command([
            'update-service', '--service', service,
            '--task-definition', task_def_name,
            '--desired-count', '2',
        ])


os.chdir(_root_dir)

parser = argparse.ArgumentParser(
    description="""Deploy latest Docker image to staging server.
The task definition file is used as the task definition, whereas
the service file is used to configure the service.
""")
parser.add_argument(
    'task-definition-file', help='Your task definition JSON file')
parser.add_argument('service-file', help='Your service JSON file')
args = parser.parse_args()

task_def_name = _register_task_definition(args.task_definition_file)
_update_service(args.service_file, task_def_name)
