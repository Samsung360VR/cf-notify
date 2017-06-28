import json
import shlex
import urllib
import urllib2
import slack
import boto3
import re
from itertools import groupby

# Mapping CloudFormation status codes to colors for Slack message attachments
# Status codes from http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-describing-stacks.html
STATUS_COLORS = {
    'CREATE_COMPLETE': '#008000',
    'CREATE_IN_PROGRESS': '#ccffcc',
    'CREATE_FAILED': 'danger',
    'DELETE_COMPLETE': '#008000',
    'DELETE_FAILED': 'danger',
    'DELETE_IN_PROGRESS': '#00ff80',
    'ROLLBACK_COMPLETE': 'warning',
    'ROLLBACK_FAILED': 'danger',
    'ROLLBACK_IN_PROGRESS': 'warning',
    'UPDATE_COMPLETE': '#008000',
    'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS': '#33ff33',
    'UPDATE_IN_PROGRESS': '#ccffcc',
    'UPDATE_ROLLBACK_COMPLETE': 'warning',
    'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS': 'warning',
    'UPDATE_ROLLBACK_FAILED': 'danger',
    'UPDATE_ROLLBACK_IN_PROGRESS': 'warning'
}

# List of CloudFormation statuses that will trigger a call to `get_stack_summary_attachment`
DESCRIBE_STACK_STATUS = [
    'CREATE_COMPLETE',
    'DELETE_IN_PROGRESS',
]

# List of CloudFormation statuses that will trigger a call to `get_stack_params_attachment`
DESCRIBE_STACK_PARAMS = [
    'UPDATE_IN_PROGRESS'
    'UPDATE_COMPLETE',
    'CREATE_IN_PROGRESS',
    'CREATE_COMPLETE',
    'DELETE_IN_PROGRESS'
    'DELETE_COMPLETE',
]

# List of properties from ths SNS message that will be included in a Slack message
SNS_PROPERTIES_FOR_SLACK = [
    'Timestamp',
    'StackName',
]

# List of parameters, tags and outputs that will be included in a Slack message (if present)
STACK_PARAMETERS_FOR_SLACK = [
    'SubdomainName',
    'aBlueOrGreen',
    'environment',
    'SubdomainName',
    'version',
    'configAppDomain',
    'configAwsBucketName',
    'configRTMPIngestAddress',
    'microservice',
    'myUri',
]

client = None


def lambda_handler(event, context):
    global client
    client = boto3.client('cloudformation')

    message = event['Records'][0]['Sns']
    sns_message = message['Message']
    cf_message = dict(token.split('=', 1) for token in shlex.split(sns_message))

    # ignore messages that do not pertain to the Stack as a whole
    if not cf_message['ResourceType'] == 'AWS::CloudFormation::Stack':
        return

    message = get_stack_update_message(cf_message)
    data = json.dumps(message)
    req = urllib2.Request(slack.WEBHOOK, data, {'Content-Type': 'application/json'})
    urllib2.urlopen(req)


def get_stack_update_message(cf_message):
    attachments = [
        get_stack_update_attachment(cf_message)
    ]
    resourse_status = cf_message['ResourceStatus']
    #if resourse_status in DESCRIBE_STACK_PARAMS:
    if not resourse_status.upper().endswith('_IN_PROGRESS'):
        attachments.append(get_stack_params_attachment(cf_message['StackName']))
    if resourse_status in DESCRIBE_STACK_STATUS:
        attachments.append(get_stack_summary_attachment(cf_message['StackName']))

    stack_url = get_stack_url(cf_message['StackId'])

    message = {
        'text': 'Stack: {stack} has entered status: {status} <{link}|(view in web console)>'.format(
                stack=cf_message['StackName'], status=cf_message['ResourceStatus'], link=stack_url),
        'attachments': attachments
    }

    channel = get_channel(cf_message['StackName'])

    if channel:
        message['channel'] = channel

    return message


def get_channel(stack_name):
    default = slack.CHANNEL if hasattr(slack, 'CHANNEL') else None

    if hasattr(slack, 'CUSTOM_CHANNELS'):
        return slack.CUSTOM_CHANNELS.get(stack_name, default)

    return default


def get_stack_update_attachment(cf_message):
    title = 'Stack {stack} is now status {status}'.format(
        stack=cf_message['StackName'],
        status=cf_message['ResourceStatus'])

    return {
        'title': title,
        'fields': [{'title': k, 'value': v, 'short': True}
                   for k, v in cf_message.iteritems() if k.lower() in map(str.lower, SNS_PROPERTIES_FOR_SLACK)],
        'color': STATUS_COLORS.get(cf_message['ResourceStatus'], '#000000'),
    }


def get_stack_summary_attachment(stack_name):
    resources = client.describe_stack_resources(StackName=stack_name)
    sorted_resources = sorted(resources['StackResources'], key=lambda res: res['ResourceType'])
    grouped_resources = groupby(sorted_resources, lambda res: res['ResourceType'])
    resource_count = {key: len(list(group)) for key, group in grouped_resources}

    title = 'Breakdown of all {} resources'.format(len(resources['StackResources']))

    return {
        'title': title,
        'fields': [{'title': 'Type {}'.format(k), 'value': 'Total {}'.format(v), 'short': False}
                   for k, v in resource_count.iteritems()]
    }


def get_stack_params_attachment(stack_name):
    stack_descr = client.describe_stacks(StackName=stack_name).get('Stacks', {})[0]

    params = {e['OutputKey']: e['OutputValue'] for e in stack_descr.get('Outputs', [])}
    params.update({e['Key']: e['Value'] for e in stack_descr.get('Tags', [])})
    params.update({e['ParameterKey']: e['ParameterValue'] for e in stack_descr.get('Parameters', [])})

    if not params:
        return None

    if params.get('environment'):
        if params['environment'].lower() == 'stage':
            params['environment'] += ' :stage:'
        elif params['environment'].lower().startswith('prod'):
            params['environment'] += ' :production:'
        else:
            params['environment'] += ' :construction:'

    if params.get('aBlueOrGreen'):
        params['aBlueOrGreen'] += ' ' + {
            'blue': ':large_blue_circle:',
            'green': ':green_apple:',
        }.get(params['aBlueOrGreen'].lower(),':alien:')

    title = 'Some parameters:'

    return {
        'title': title,
        'fields': [{'title': k, 'value': v, 'short': False}
                   for k, v in params.iteritems() if k.lower() in map(str.lower, STACK_PARAMETERS_FOR_SLACK)]
    }


def get_stack_region(stack_id):
    regex = re.compile('arn:aws:cloudformation:(?P<region>[a-z]{2}-[a-z]{4,9}-[1-2]{1})')
    return regex.match(stack_id).group('region')


def get_stack_url(stack_id):
    region = get_stack_region(stack_id)

    query = {
        'filter': 'active',
        'tab': 'events',
        'stackId': stack_id
    }

    return ('https://{region}.console.aws.amazon.com/cloudformation/home?region={region}#/stacks?{query}'
            .format(region=region, query=urllib.urlencode(query)))

# client = boto3.client('cloudformation')
# print get_stack_params_attachment('stage-route-goggles')