#!/bin/bash
# LocalStack bootstrap — runs automatically on container startup via the
# /etc/localstack/init/ready.d/ hook.
#
# Creates the SNS topic, SQS queue, and S3 bucket required by the shadow proxy.
# The SNS subscription wires the topic to the queue so a single publish()
# call fans out to all candidate-model queues.

set -euo pipefail

ENDPOINT="http://localhost:4566"
REGION="us-east-1"
ACCOUNT="000000000000"

AWS="aws --endpoint-url=$ENDPOINT --region=$REGION"

echo "[init] Creating SNS topic: shadow-events"
TOPIC_ARN=$($AWS sns create-topic --name shadow-events --query TopicArn --output text)
echo "[init] Topic ARN: $TOPIC_ARN"

echo "[init] Creating SQS queue: shadow-tasks"
QUEUE_URL=$($AWS sqs create-queue --queue-name shadow-tasks --query QueueUrl --output text)
QUEUE_ARN="arn:aws:sqs:$REGION:$ACCOUNT:shadow-tasks"
echo "[init] Queue URL: $QUEUE_URL"

echo "[init] Subscribing SQS queue to SNS topic"
$AWS sns subscribe \
    --topic-arn "$TOPIC_ARN" \
    --protocol sqs \
    --notification-endpoint "$QUEUE_ARN"

echo "[init] Creating S3 bucket: shadow-events"
$AWS s3 mb s3://shadow-events

echo "[init] LocalStack resources ready"
echo "[init]   SNS_SHADOW_TOPIC_ARN=$TOPIC_ARN"
echo "[init]   SQS_QUEUE_URL=$QUEUE_URL"
echo "[init]   S3_BUCKET=shadow-events"
