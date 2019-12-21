from __future__ import print_function

import json
import urllib
import boto3
from botocore.exceptions import ClientError
import datetime
import os
import time
import re

bastion_cluster = os.environ['BASTION_CLUSTER']
subnet_string = os.environ['BASTION_SUBNETS']
subnet_array = subnet_string.split(',')
vpc = os.environ['BASTION_VPC']
task_definition_name = os.environ['BASTION_TASK_DEFINITION_NAME']

def ipResponse(ip):
    response = {}
    response['statusCode'] = 200
    response['body'] = ip
    return response

def failResponse(error):
    response = {}
    response['statusCode'] = 500
    response['body'] = error
    return response

def lambda_handler(event, context):
    user = event['queryStringParameters']['user']
    ip = event['requestContext']['identity']['sourceIp'] + "/32"
    ec2 = boto3.client('ec2')
    ecs = boto3.client('ecs')

    bastion_name = 'bastion-' + user

    try:
        # Check if everything already exists, if so return that
        sg_check = ec2.describe_security_groups(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc]},
                {'Name': 'group-name', 'Values': [bastion_name]}
            ]
        )
        running_tasks = ecs.list_tasks(
            cluster=bastion_cluster,
            family=task_definition_name,
            desiredStatus='RUNNING'
        )
        if len(running_tasks['taskArns']) > 0:
            tasklist = ecs.describe_tasks(cluster=bastion_cluster, tasks=running_tasks['taskArns'])
            task_arn = tasklist['tasks'][0]['taskArn']
            attachment_id = tasklist['tasks'][0]['attachments'][0]['id']
            attachment_identifier = "attachment/" + attachment_id
            attachment_description = re.sub(r'task/.*', attachment_identifier, task_arn)
            eni_description = ec2.describe_network_interfaces(
                Filters=[
                    {
                        'Name': 'description',
                        'Values': [attachment_description]
                    }
                ]
            )
            ip = eni_description['NetworkInterfaces'][0]['Association']['PublicIp']

            return ipResponse(ip)
    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.NotFound':
            print("SecurityGroup doesn't exist yet")
        else:
            failResponse(e.response)

    if len(sg_check['SecurityGroups']) != 0:
        # TODO: if securitygroup found, but no task delete the securitygroup before creating the new one
        if sg_check['SecurityGroups'][0]['IpPermissions'][0]['IpRanges'][0]['CidrIp'] != ip:
            # Create the security group
            sg_response = ec2.create_security_group(
                Description='Bastion access for ' + user,
                GroupName=bastion_name,
                VpcId=vpc
            )

            sg = sg_response['GroupId']

            # Add the ingress rule to it
            ec2.authorize_security_group_ingress(
                CidrIp=ip,
                FromPort=22,
                GroupId=sg,
                IpProtocol='tcp',
                ToPort=22
            )
        else:
            # Existing security group matches, so use it
            sg = sg_check['SecurityGroups'][0]['GroupId']

    # Start the bastion container
    response = ecs.run_task(
        cluster=bastion_cluster,
        taskDefinition=task_definition_name,
        count=1,
        startedBy='bastion-builder',
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': subnet_array,
                'securityGroups': [sg],
                'assignPublicIp': 'ENABLED'
            }
        }
    )
    task_arn = response['tasks'][0]['taskArn']
    attachment_id = response['tasks'][0]['attachments'][0]['id']
    attachment_identifier = "attachment/" + attachment_id
    attachment_description = re.sub(
        r'task/.*', attachment_identifier, task_arn)

    # It takes a bit of time to get the ENI, check after a couple of seconds and then loop
    time.sleep(2)
    eni_description = ec2.describe_network_interfaces(
        Filters=[
            {
                'Name': 'description',
                'Values': [attachment_description]
            }
        ]
    )
    while (len(eni_description['NetworkInterfaces']) == 0 or eni_description['NetworkInterfaces'][0]['Attachment']['Status'] != 'attached'):
        time.sleep(2)
        eni_description = ec2.describe_network_interfaces(
            Filters=[
                {
                    'Name': 'description',
                    'Values': [attachment_description]
                }
            ]
        )
    ip = eni_description['NetworkInterfaces'][0]['Association']['PublicIp']

    return ipResponse(ip)
