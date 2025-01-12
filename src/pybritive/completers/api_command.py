import typing as t
import inspect
from britive.britive import Britive
import pkg_resources


def get_dynamic_method_parameters(method):
    try:
        # create an instance of the Britive class, so we can inspect it
        # this doesn't need to actually connect to any tenant, and we couldn't even if we
        # wanted to since when performing shell completion we have no tenant/token
        # context in order to properly establish a connection.
        b = Britive(token='ignore', tenant='britive.com', query_features=False)

        # parse the method, so we can determine where in the "hierarchy" we are
        # and what commands/subcommands the user should be presented with
        for part in method.split('.'):
            b = getattr(b, part)

        params = {}
        spec = inspect.getfullargspec(b)

        # reformat parameters into a more consumable dict while holds all the required details
        helper = spec[6]
        helper.pop('return', None)
        for param, param_type in helper.items():
            params[param] = {
                'type': str(param_type).split("'")[1]
            }

        defaults = list(spec[3])
        names = list(spec[0])

        if len(defaults) > 0:
            for i in range(1, len(defaults) + 1):
                name = names[-1 * i]
                default = defaults[-1 * i]
                params[name]['default'] = default

        try:  # we don't REALLY need the doc string so if there are errors just eat them and move on
            doc_lines = inspect.getdoc(b)
            doc_lines = doc_lines.replace(':returns:', 'RETURNSPLIT')
            doc_lines = doc_lines.replace(':return:', 'RETURNSPLIT')
            doc_lines = doc_lines.split('RETURNSPLIT')[0].split(':param ')[1:]

            for line in doc_lines:
                helper = line.split(':')
                name = helper[0].strip()
                help_text = ''.join(helper[1].strip().splitlines()).replace('    ', ' ')
                params[name]['help'] = help_text
        except:
            pass

        param_list = []

        for name, values in params.items():
            help_text = values.get('help') or ''

            if 'default' in values.keys():  # cannot do a .get('default') as the default value could be False/None/etc.
                preamble = f'[optional: default = {values["default"]}]'
                if help_text == '':
                    help_text = preamble
                else:
                    help_text = f'{preamble} - {help_text}'

            param = {
                'flag': f'--{name.replace("_", "-")}',
                'help': help_text
            }

            param_list.append(param)

        return param_list
    except Exception as e:
        return []


def command_api_patch_shell_complete(cls):
    # click < 8.0.0 does shell completion different...
    # not all the classes/decorators are available, so we cannot
    # create custom shell completions like we can with click > 8.0.0
    major, minor, patch = pkg_resources.get_distribution('click').version.split('.')[0:3]

    # we cannot patch the shell_complete method because it does not exist (click 7.x doesn't have it)
    # future proofing this as well in case click 9.x changes things up a lot
    if int(major) != 8:
        return

    # we could potentially patch but there could be changes to shell_complete method which are not
    # accounted for in this patch - we will have to manually review any changes and ensure they are
    # backwards compatible.
    if int(minor) != 1:
        return

    from click.shell_completion import CompletionItem
    from click.core import ParameterSource
    from click import Context, Option

    # https://stackoverflow.com/questions/43778914/python3-using-super-in-eq-methods-raises-runtimeerror-super-class
    __class__ = cls  # provide closure cell for super()

    def shell_complete(self, ctx: Context, incomplete: str) -> t.List["CompletionItem"]:
        from click.shell_completion import CompletionItem

        results: t.List["CompletionItem"] = []

        if incomplete and not incomplete[0].isalnum():
            method = ctx.params.get('method')
            if method:
                dynamic_params = get_dynamic_method_parameters(method)

                results.extend(
                    CompletionItem(p['flag'], help=p['help'])
                    for p in dynamic_params if p['flag'].startswith(incomplete)
                )

            for param in self.get_params(ctx):
                if (
                    not isinstance(param, Option)
                    or param.hidden
                    or (
                        not param.multiple
                        and ctx.get_parameter_source(param.name)  # type: ignore
                        is ParameterSource.COMMANDLINE
                    )
                ):
                    continue

                results.extend(
                    CompletionItem(name, help=param.help)
                    for name in [*param.opts, *param.secondary_opts]
                    if name.startswith(incomplete)
                )

        results.extend(super().shell_complete(ctx, incomplete))

        return results

    cls.shell_complete = shell_complete
