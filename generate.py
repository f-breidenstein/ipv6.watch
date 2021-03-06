#!/usr/bin/env python3
import argparse
import asyncio
import datetime
import logging
import os
from pprint import pformat

import aiodns
import jsonschema
import yaml
from jinja2 import Environment, FileSystemLoader, Template

logger = logging.getLogger(__name__)

config_schema = {
    'type': 'Object',
    'attributes': {
        'nameservers': {
            'type': 'Object'
        },
        'targets': {
            'type': 'Object'
        },
        'messages': {
            'type': 'Object'
        }
    }
}


def writeable_dir(values):
    prospective_dir = values
    if not os.path.isdir(prospective_dir):
        raise argparse.ArgumentTypeError(
            "writeable_dir:{0} is not a valid path".format(prospective_dir))
    if not os.access(prospective_dir, os.W_OK | os.R_OK):
        raise argparse.ArgumentTypeError(
            "writeable_dir:{0} is not a writeable dir".format(prospective_dir))
    return prospective_dir


def prepare_resolvers(nameservers, loop=None):
    if not loop:
        loop = asyncio.get_event_loop()

    resolvers = {}
    for name, servers in nameservers.items():
        resolvers[name] = list(
            (server, aiodns.DNSResolver(loop=loop, nameservers=[server]))
            for server in servers)

    return resolvers


async def resolve_host(target, resolver, context=None):
    try:
        response = await resolver.query(target, 'AAAA')
    except aiodns.error.DNSError:
        return False, context
    if len(response) == 0:
        return False, context
    return True, context


async def resolve_target(target, resolvers):
    tasks = []
    for host in target['hosts']:
        for name, r in resolvers.items():
            for resolver in r:
                tasks.append(
                    resolve_host(
                        host, resolver[1],
                        (host, name, resolver[0])))

    results = {}
    r = await asyncio.wait(tasks)
    for l in r:
        for task in l:
            result = task.result()

            response, context = task.result()
            host, resolver_name, nameserver = context

            if response:
                logger.info('\033[0;32m✓\033[0m\t%s @ %s', host, nameserver)
            else:
                logger.info('\033[0;31m✗\033[0m\t%s @ %s', host, nameserver)

            h = results[host] = results.get(host, {})
            r = h[resolver_name] = h.get(resolver_name, {})
            r[nameserver] = response

    return results


def generate_message(media, target, conf, result):
    if media in conf:
        template = Template(media.get(result))
        return template.render(
            target=target,
            conf=conf,
            result=result,
            media=media)
    else:
        raise RuntimeError('Invalid media {} for {}'.format(media, target))


async def handle_target(resolvers, name, target):
    result = await resolve_target(target, resolvers)
    msg = "none"

    if any(
            success
            for host, rs in result.items()
            for resolver, servers in rs.items()
            for server, success in servers.items()
    ):
        msg = "some"

    if all(
            success
            for host, rs in result.items()
            for resolver, servers in rs.items()
            for server, success in servers.items()
    ):
        msg = "all"

    return name, dict(hosts=result, summary=msg)



async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-c',
        '--config',
        dest='config',
        default='conf.yaml',
        type=argparse.FileType('r'))
    parser.add_argument(
        '-l',
        '--log-level',
        dest='log_level',
        choices=[
            'DEBUG',
            'ERROR',
            'INFO',
            'WARN'],
        help='Debug level',
        default='INFO')
    parser.add_argument('dest', default='dist', type=writeable_dir)

    args = parser.parse_args()

    log_level = getattr(logging, args.log_level)
    logging.basicConfig(level=log_level)

    config = yaml.load(args.config)
    # TODO: add item validation
    jsonschema.validate(config_schema, config)

    nameservers = config['nameservers']
    targets = config['targets']

    loop = asyncio.get_event_loop()

    resolvers = prepare_resolvers(nameservers, loop)

    results = {}
    tasks = []
    for name, target in targets.items():
        tasks.append(handle_target(resolvers, name, target))

    tasks = await asyncio.wait(tasks)
    for task_list in tasks:
        for task in task_list:
            name, result = task.result()
            results[name] = result


    results = sorted(results.items(), key=lambda x: x[0])
    logging.debug(pformat(results))
    jinja_env = Environment(loader=FileSystemLoader('templates/'))
    template = jinja_env.get_template('index.jinja2')
    with open(os.path.join(args.dest, 'index.html'), 'w') as fh:
        fh.write(
            template.render(
                long_date=datetime.datetime.now().strftime('%B %Y'),
                results=results,
                targets=targets,
                messages=config['messages'],
                date=datetime.datetime.utcnow()))


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
