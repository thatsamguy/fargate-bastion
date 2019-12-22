from __future__ import print_function

import urllib
import boto3
from botocore.exceptions import ClientError
import datetime
import os
import time
import re

bastion_cluster = os.environ['BASTION_CLUSTER']
vpc = os.environ['BASTION_VPC']

def successResponse():
    response = {}
    response['statusCode'] = 200
    return response

def failResponse(error):
    response = {}
    response['statusCode'] = 500
    response['body'] = error
    return response

def lambda_handler(event, context):
    ec2 = boto3.client('ec2')
    ecs = boto3.client('ecs')

    print("Starting to delete")

    try:
        # Find the Fargate task
        running_tasks = ecs.list_tasks(
            cluster=bastion_cluster,
            startedBy='bastion-builder',
            desiredStatus='RUNNING'
        )
        related_securitygroups = []
        enis = []
        for task_arn in running_tasks['taskArns']:
            print(task_arn)
            # Retrieve the ENI information
            tasklist = ecs.describe_tasks(
                cluster=bastion_cluster, tasks=[task_arn], include=['TAGS'])
            for tag in tasklist['tasks'][0]['tags']:
                if tag['key'] == 'name':
                    print(tag['value'])
            attachment_id = tasklist['tasks'][0]['attachments'][0]['id']
            attachment_identifier = "attachment/" + attachment_id
            attachment_description = re.sub(
                r'task/.*', attachment_identifier, task_arn)
            enis.append(attachment_description)
            eni_description = ec2.describe_network_interfaces(
                Filters=[
                    {
                        'Name': 'description',
                        'Values': [attachment_description]
                    }
                ]
            )
            for eni in eni_description['NetworkInterfaces']:
                for eni_sg in eni['Groups']:
                    related_securitygroups.append(eni_sg['GroupId'])

            # Stop the task
            ecs.stop_task(
                cluster=bastion_cluster,
                task=task_arn,
                reason='Requested by user'
            )

        # Wait until the ENI is deleted to prevent dependency conflict for removing security group
        eni_description = ec2.describe_network_interfaces(
            Filters=[
                {
                    'Name': 'description',
                    'Values': enis
                }
            ]
        )
        while len(eni_description['NetworkInterfaces']) > 0:
            time.sleep(2)
            eni_description = ec2.describe_network_interfaces(
                Filters=[
                    {
                        'Name': 'description',
                        'Values': [attachment_description]
                    }
                ]
            )
        print("All tasks are deleted")

        print("Deleting related security groups")

        for group_id in related_securitygroups:
            print(group_id)
            ec2.delete_security_group(GroupId=group_id)

        print("Groups are deleted")

    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidGroup.NotFound':
            print("SecurityGroup doesn't exist, skipping deletion")
        else:
            failResponse(e.response)

    return successResponse()
