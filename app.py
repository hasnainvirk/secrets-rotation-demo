#!/usr/bin/env python3
import aws_cdk as cdk

from secrets_rotation_demo.secrets_rotation_demo_stack import SecretsRotationDemoStack


app = cdk.App()
SecretsRotationDemoStack(
    app,
    "SecretsRotationDemoStack",
)

app.synth()
