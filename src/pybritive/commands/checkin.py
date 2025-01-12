import click
from ..helpers.build_britive import build_britive
from ..options.britive_options import britive_options
from ..helpers.profile_argument_dectorator import click_smart_profile_argument


@click.command()
@build_britive
@britive_options(names='tenant,token,silent,passphrase,federation_provider')
@click_smart_profile_argument
def checkin(ctx, tenant, token, silent, passphrase, federation_provider, profile):
    """Checkin a profile.

    This command takes 1 required argument `PROFILE`. This should be a string representation of the profile
    that should be checked in. Format is `application name/environment name/profile name`.
    """
    ctx.obj.britive.checkin(
        profile=profile
    )


