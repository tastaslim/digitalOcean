#!/bin/bash
# LocalStack bootstrap — runs automatically on container startup via the
# /etc/localstack/init/ready.d/ hook.
#
# Mirrors the production SQS/SNS topology so local behaviour matches prod:
#   - SNS topic   shadow-events
#   - SQS DLQ     shadow-tasks-dlq        (dead-letter queue)
#   - SQS queue   shadow-tasks            (RedrivePolicy -> DLQ after N receives)
#   - S3 bucket   shadow-events
#
# In production these same resources are provisioned by Terraform/CDK against
# real AWS (see README "Production SQS + DLQ"); this script just reproduces the
# identical wiring against LocalStack for local/integration testing.

set -euo pipefail

ENDPOINT="http://localhost:4566"
REGION="us-east-1"
ACCOUNT="000000000000"

# Retry/redrive tuning — keep in sync with the Terraform values in the README.
MAX_RECEIVE_COUNT="3"      # deliveries before a message is moved to the DLQ
VISIBILITY_TIMEOUT="60"    # seconds a message is hidden while being processed
RECEIVE_WAIT="20"          # long-poll seconds (matches SQSAdapter WaitTimeSeconds)

AWS="aws --endpoint-url=$ENDPOINT --region=$REGION"

echo "[init] Creating SNS topic: shadow-events"
TOPIC_ARN=$($AWS sns create-topic --name shadow-events --query TopicArn --output text)
echo "[init] Topic ARN: $TOPIC_ARN"

# ── Dead-letter queue first: the main queue's RedrivePolicy needs its ARN ──
echo "[init] Creating SQS dead-letter queue: shadow-tasks-dlq"
DLQ_URL=$($AWS sqs create-queue --queue-name shadow-tasks-dlq --query QueueUrl --output text)
DLQ_ARN=$($AWS sqs get-queue-attributes \
    --queue-url "$DLQ_URL" \
    --attribute-names QueueArn \
    --query 'Attributes.QueueArn' --output text)
echo "[init] DLQ ARN: $DLQ_ARN"

# ── Main queue with redrive policy + visibility/long-poll tuning ──────────
# RedrivePolicy is itself a JSON string nested inside the attributes JSON, so
# its inner quotes must be escaped before embedding.
REDRIVE_POLICY="{\"deadLetterTargetArn\":\"${DLQ_ARN}\",\"maxReceiveCount\":\"${MAX_RECEIVE_COUNT}\"}"
REDRIVE_ESCAPED=$(printf '%s' "$REDRIVE_POLICY" | sed 's/"/\\"/g')
QUEUE_ATTRS="{\"VisibilityTimeout\":\"${VISIBILITY_TIMEOUT}\",\"ReceiveMessageWaitTimeSeconds\":\"${RECEIVE_WAIT}\",\"RedrivePolicy\":\"${REDRIVE_ESCAPED}\"}"

echo "[init] Creating SQS queue: shadow-tasks (maxReceiveCount=${MAX_RECEIVE_COUNT} -> DLQ)"
QUEUE_URL=$($AWS sqs create-queue \
    --queue-name shadow-tasks \
    --attributes "$QUEUE_ATTRS" \
    --query QueueUrl --output text)
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
echo "[init]   SQS_DLQ_URL=$DLQ_URL  (maxReceiveCount=${MAX_RECEIVE_COUNT})"
echo "[init]   S3_BUCKET=shadow-events"
