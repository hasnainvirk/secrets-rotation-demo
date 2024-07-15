from dotenv import load_dotenv
import os
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_rds as rds,
    Duration,
    CfnOutput,
    aws_secretsmanager as sm,
    aws_lambda as lambda_,
    aws_iam as iam,
    Tags,
)
from constructs import Construct

load_dotenv()

database_user = os.getenv("DATABASE_USER")
region = os.getenv("REGION")
keypair_name = os.getenv("KEY_PAIR_NAME")


class SecretsRotationDemoStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create a VPC with 1 public subnet in each AZ
        vpc = ec2.Vpc(
            self,
            "DemoVPC",
            max_azs=3,  # Adjust the number of AZs as needed
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PublicSubnet",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,  # Adjust CIDR mask as needed
                ),
                ec2.SubnetConfiguration(
                    name="PrivateSubnet",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,  # Adjust CIDR mask as needed
                ),
            ],
            nat_gateways=0,
        )

        # Create a VPC endpoint for Secrets Manager
        secrets_manager_endpoint = ec2.InterfaceVpcEndpoint(
            self,
            "SecretsManagerEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointService(
                f"com.amazonaws.{region}.secretsmanager", 443
            ),
            private_dns_enabled=True,  # Enable private DNS for the endpoint
        )

        # Create an Aurora Serverless MySQL database
        aurora_serverless_cluster = rds.ServerlessCluster(
            self,
            "DemoAuroraServerlessMySQLCluster",
            engine=rds.DatabaseClusterEngine.AURORA_MYSQL,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            scaling=rds.ServerlessScalingOptions(
                auto_pause=Duration.hours(0),  # Disablinf automatica pause
                min_capacity=rds.AuroraCapacityUnit.ACU_2,  # Minimum capacity
                max_capacity=rds.AuroraCapacityUnit.ACU_16,  # Maximum capacity
            ),
            default_database_name="DemoAuroraServerlessMySQLDatabase",
        )

        # Extract the DB cluster identifier and DB endpoint for the secret
        db_cluster_id = (
            aurora_serverless_cluster.cluster_identifier
        )  # Extract the DB cluster identifier for the secret
        db_endpoint = (
            aurora_serverless_cluster.cluster_endpoint.hostname
        )  # Extract the DB endpoint for the secret
        db_port = (
            aurora_serverless_cluster.cluster_endpoint.port
        )  # Extract the DB port for the secret

        db_sg = aurora_serverless_cluster.connections.security_groups[
            0
        ]  # Extract the security group for the database

        # Create a secret in AWS Secrets Manager for a Database user
        db_user_secret = sm.Secret(
            self,
            "DemoMySQLAdminPassword",
            description="Aurora Serverless admin password",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template=f'{{"username": "{database_user}", "password": "password", "engine": "mysql", "host": "{db_endpoint}", "port": {db_port}, "dbClusterIdentifier": "{db_cluster_id}"}}',
                generate_string_key="password",
                exclude_characters="\"@/\\ '",
            ),
        )

        # Define the policy statement
        policy_statement = iam.PolicyStatement(
            actions=[
                "secretsmanager:DescribeSecret",
                "secretsmanager:GetSecretValue",
                "secretsmanager:PutSecretValue",
                "secretsmanager:UpdateSecretVersionStage",
                "secretsmanager:GetRandomPassword",
            ],
            resources=["*"],
            effect=iam.Effect.ALLOW,
        )

        # Create the IAM policy
        secrets_manager_policy = iam.Policy(
            self, "SecretsManagerPolicy", statements=[policy_statement]
        )

        lambda_execution_role = iam.Role(
            self,
            "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Role that grants the Lambda function permissions to access Secrets Manager, CloudWatch, and basic Lambda execution permissions.",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "CloudWatchLogsFullAccess"
                ),
            ],
        )

        # Attach the policy to the role
        secrets_manager_policy.attach_to_role(lambda_execution_role)

        # Define a security group for the Lambda function that allows database access
        lambda_sg_db = ec2.SecurityGroup(
            self,
            "LambdaToDB",
            vpc=vpc,
            description="Allow Lambda to access database",
            security_group_name="LambdaToDB",
            allow_all_outbound=False,
        )

        lambda_sg_db.add_egress_rule(
            connection=ec2.Port.tcp(3306),
            peer=ec2.Peer.any_ipv4(),
            description="Allow outbound traffic to Database",
        )

        db_sg.add_ingress_rule(
            connection=ec2.Port.tcp(3306),
            peer=lambda_sg_db,
            description="Allow Lambda to access the database",
        )

        lambda_sg_https = ec2.SecurityGroup(
            self,
            "LambdaToSecretsManager",
            vpc=vpc,
            description="Allow Lambda to access HTTPS",
            security_group_name="LambdaToSecretsManager",
            allow_all_outbound=False,
        )

        lambda_sg_https.add_ingress_rule(
            connection=ec2.Port.HTTPS,
            peer=ec2.Peer.any_ipv4(),
            description="Allow Lambda to access Secrets Manager",
        )

        lambda_sg_https.add_egress_rule(
            connection=ec2.Port.HTTPS,
            peer=ec2.Peer.any_ipv4(),
            description="Allow all traffic to any destination",
        )

        # Define the Lambda function for rotating the secret
        rotation_lambda = lambda_.Function(
            self,
            "RotationLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset(
                "lambda/function.zip"
            ),  # Specify the path to your Lambda function code
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            role=lambda_execution_role,
            security_groups=[lambda_sg_db, lambda_sg_https],
            environment={
                "EXCLUDE_CHARACTERS": "/@\"'\\",
                "EXCLUDE_LOWERCASE": "false",
                "EXCLUDE_NUMBERS": "false",
                "EXCLUDE_PUNCTUATION": "false",
                "EXCLUDE_UPPERCASE": "false",
                "PASSWORD_LENGTH": "32",
                "REQUIRE_EACH_INCLUDED_TYPE": "true",
                "SECRETS_MANAGER_ENDPOINT": f"https://secretsmanager.{region}.amazonaws.com",
            },
            timeout=Duration.seconds(300),
        )

        # Grant AWS Secrets Manager permission to invoke the Lambda function
        rotation_lambda.add_permission(
            "AllowInvocationFromSecretsManager",
            principal=iam.ServicePrincipal("secretsmanager.amazonaws.com"),
            action="lambda:InvokeFunction",
        )

        # set up automatic rotation for the secret
        db_user_secret.add_rotation_schedule(
            "RotationSchedule",
            rotation_lambda=rotation_lambda,
            automatically_after=Duration.days(30),  # Rotate every 30 days, for example
            rotate_immediately_on_update=False,
        )

        # Create UserData section for the bastion host
        user_data = ec2.UserData.for_linux()
        user_data.add_commands("sudo dnf update -y", "sudo dnf install mariadb105 -y")

        # Create a security group for the bastion host
        bastion_sg = ec2.SecurityGroup(
            self,
            "BastionHostSG",
            vpc=vpc,
            description="Allow ssh access to ec2 instances",
            allow_all_outbound=True,
        )
        bastion_sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(22),
            description="Allow inbound SSH traffic",
        )

        # Allow the bastion host to connect to the database
        db_sg.add_ingress_rule(
            peer=bastion_sg,
            connection=ec2.Port.tcp(3306),
            description="Allow MySQL access from bastion host",
        )

        # Create a bastion host
        bastion_host = ec2.Instance(
            self,
            "BastionHost",
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            key_pair=ec2.KeyPair.from_key_pair_attributes(
                self, "BastionHostKeyPair", key_pair_name=keypair_name
            ),  # Ensure you have this key pair created in your AWS account
            user_data=user_data,
            security_group=bastion_sg,
        )

        Tags.of(self).add("Project", "SecretsRotationDemo")
        Tags.of(self).add("Environment", "Test")

        # Output the bastion host public IP
        CfnOutput(self, "BastionHostPublicIP", value=bastion_host.instance_public_ip)
        CfnOutput(self, "DBClusterIdentifier", value=db_cluster_id)
        CfnOutput(self, "DBEndpoint", value=db_endpoint)
