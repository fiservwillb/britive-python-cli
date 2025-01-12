import click

# eval example: eval $(pybritive checkout test -m env)

browser_choices = click.Choice(
    [
        'default'
        'mozilla',
        'firefox',
        'windows-default',
        'macosx',
        'safari',
        'chrome',
        'chromium'
    ],
    case_sensitive=False
)

