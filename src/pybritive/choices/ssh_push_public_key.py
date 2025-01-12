import click

# eval example: eval $(pybritive checkout test -m env)

ssh_push_public_key_choices = click.Choice(
    [
        'ec2-instance-connect',
        'os-login',
        'instance-metadata',
        'default'  # used in flag_value to make the option look like a flag - each CSP can determine what default means
    ],
    case_sensitive=False
)

