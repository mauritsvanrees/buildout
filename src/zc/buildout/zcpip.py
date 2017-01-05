from pip._vendor.packaging.utils import canonicalize_name
from pip.download import PipSession
from pip.index import FormatControl
from pip.index import PackageFinder
from pip.locations import distutils_scheme
from pip.req import InstallRequirement
from pip.req import RequirementSet
from pip.utils import get_installed_version
from pip.utils.build import BuildDirectory
from pip.wheel import WheelBuilder
from pip.wheel import WheelCache

import logging
import operator


logger = logging.getLogger('zc.buildout.zcpip')

# pip._vendor.distlib.index.DEFAULT_INDEX = 'https://pypi.python.org/pypi'
# But that should be 'simple'.  Or we get it from buildout.
DEFAULT_INDEX = 'https://pypi.python.org/simple'

# FormatControl is a namedtuple with no_binary and only_binary.  In no_binary
# we should put package names that are known to fail when installed as wheels.
# For example, 'pip install zc.recipe.egg' gives an error:
# 'zc.recipe.egg is in an unsupported or invalid wheel'
#
# We probably want to do this differently, for example for each spec try to
# install as wheel, and if it fails try as source distribution.  But for the
# moment we can hardcode this.
STANDARD_FORMAT_CONTROL = FormatControl(set(), set())
BUILDOUT_FORMAT_CONTROL = FormatControl(
    set([canonicalize_name('zc.recipe.egg')]), set())
# Alternatively:
# from pip.index import fmt_ctl_handle_mutual_exclude
# fmt_ctl_handle_mutual_exclude(
#     'zc.recipe.egg',
#     STANDARD_FORMAT_CONTROL.no_binary,
#     STANDARD_FORMAT_CONTROL.only_binary)


def _build_session(cache=None, retries=None, insecure_hosts=None):
    # Do roughly what pip.basecommand.Command._build_session does.
    session = PipSession(
        cache=cache,
        retries=retries,
        insecure_hosts=insecure_hosts if insecure_hosts else [],
    )
    return session


def _build_package_finder(session):
    """Create a package finder appropriate to this requirement command.

    Do roughly what
    pip.basecommand.RequirementCommand._build_package_finder does.
    """
    return PackageFinder(
        find_links=[],
        index_urls=[DEFAULT_INDEX],
        session=session,
        format_control=BUILDOUT_FORMAT_CONTROL,
    )


def _get_lib_location_guesses(*args, **kwargs):
    # from pip.commands.install.get_lib_location_guesses.
    scheme = distutils_scheme('', *args, **kwargs)
    return [scheme['purelib'], scheme['platlib']]


def install(specs,
            versions=None,
            ):
    """Install packages according to the specifications.

    Do roughly what pip.commands.install.InstallCommand.run does.

    TODO:
    - We are not passed any versions from buildout.
    - When installing zc.recipe.egg this way, I get an error:
      UnsupportedWheel: zc.recipe.egg is in an unsupported or invalid wheel.
      It *does* download the tar.gz file.

    Ah, on the command line this fails:

      pip install  zc.recipe.egg

    and this works:

      pip install --no-binary zc.recipe.egg zc.recipe.egg

    We can do the same with format_control.  Done.
    But: you do get an error after zc.recipe egg is installed.
    Getting the entry point fails with:

    ImportError: No module named recipe.egg

    When you run buildout again, it works.
    """

    with _build_session() as session:
        finder = _build_package_finder(session)

        # It fails without a wheel cache.
        cache_dir = 'wheel-cache'
        wheel_cache = WheelCache(
            cache_dir=cache_dir,
            format_control=BUILDOUT_FORMAT_CONTROL,
        )

        # It fails without a build directory.
        # build_dir_path = 'build' gives problems.
        # With None, a temporary directory is created.
        build_dir_path = None
        with BuildDirectory(build_dir_path) as build_dir:

            # Also, in some cases you can go without a src_dir, and in other
            # cases it fails when passing None.
            # Let's do download_dir too.
            # The dirs need to exist already.

            requirement_set = RequirementSet(
                build_dir=build_dir,
                src_dir='src',
                download_dir='download',
                session=session,
                wheel_cache=wheel_cache,
            )

            # Do roughly what
            # pip.basecommand.RequirementCommand.populate_requirement_set does.
            # Get version constraints.
            if versions is not None:
                for name, version in versions.items():
                    # TODO: we may want to get the buildout['develop'] lines
                    # separately and call InstallRequirement.from_editable
                    # instead.
                    editable = True if 'dev' in version else False
                    if editable:
                        # This just causes too many problems currently, using a
                        # zc.buildout dev release, which it then tries to find
                        # on PyPI.
                        logger.warn(
                            'Ignoring editable constraint %s = %s',
                            name, version)
                        continue
                    # Collecting zc.recipe.egg==>=2.0.0a3 fails for me, even
                    # when it is already installed as dev version.  Pip says it
                    # can't find it in a list that does actually contain it...
                    if '>=' in version:
                        logger.warn(
                            'Ignoring ">=" constraint %s = %s',
                            name, version)
                        continue
                    req = InstallRequirement.from_line(
                        '{} == {}'.format(name, version),
                        constraint=True)
                    # req.editable = editable
                    requirement_set.add_requirement(req)
            # Get requirements.
            for spec in specs:
                req = InstallRequirement.from_line(spec, constraint=False)
                requirement_set.add_requirement(req)

            if not requirement_set.has_requirements:
                # Nothing to do.
                return

            # build wheels before install.
            wb = WheelBuilder(
                requirement_set,
                finder,
                build_options=[],
                global_options=[],
            )
            # Ignore the result: a failed wheel will be
            # installed from the sdist/vcs whatever.
            wb.build(autobuilding=True)

            # Install the requirements.  This is where it fails for
            # zc.recipe.egg when installing as wheel.
            # XXX In req_set.py these lines can easily fail because
            # they may be trying to add a tuple and a list:
            # global_options += self.options.get('global_options', [])
            # install_options += self.options.get('install_options', [])
            requirement_set.install(install_options=[], global_options=[])

            possible_lib_locations = _get_lib_location_guesses()

            reqs = sorted(
                requirement_set.successfully_installed,
                key=operator.attrgetter('name'))
            items = []
            for req in reqs:
                item = req.name
                try:
                    installed_version = get_installed_version(
                        req.name, possible_lib_locations
                    )
                    if installed_version:
                        item += '-' + installed_version
                except Exception:
                    pass
                items.append(item)
            installed = ' '.join(items)
            if installed:
                logger.info('Successfully installed %s', installed)

        return requirement_set
