import aws_cdk as core
import aws_cdk.assertions as assertions

from secrets_rotation_demo.secrets_rotation_demo_stack import SecretsRotationDemoStack

# example tests. To run these tests, uncomment this file along with the example
# resource in secrets_rotation_demo/secrets_rotation_demo_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = SecretsRotationDemoStack(app, "secrets-rotation-demo")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
