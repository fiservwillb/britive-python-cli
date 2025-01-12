import click
import pkg_resources
from ..completers.api import api_completer

click_major_version = int(pkg_resources.get_distribution('click').version.split('.')[0])


def click_smart_api_method_argument(func):
    if click_major_version >= 8:
        dec = click.argument('method', shell_complete=api_completer)
    else:
        dec = click.argument('method')
    return dec(func)


