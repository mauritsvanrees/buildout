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
        format_control=FormatControl(set(), set()),
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

    """

    with _build_session() as session:
        finder = _build_package_finder(session)

        # It fails without a wheel cache.
        cache_dir = 'wheel-cache'
        wheel_cache = WheelCache(
            cache_dir=cache_dir,
            format_control=FormatControl(set(), set()),
        )

        # It fails without a build directory.
        build_dir_name = 'build'
        build_dir_name = None
        with BuildDirectory(build_dir_name) as build_dir:

            requirement_set = RequirementSet(
                build_dir=build_dir,
                src_dir=None,
                download_dir=None,
                session=session,
                wheel_cache=wheel_cache,
            )

            # Do roughly what
            # pip.basecommand.RequirementCommand.populate_requirement_set does.
            # Get version constraints.
            if versions is not None:
                for name, version in versions.items():
                    req = InstallRequirement.from_line(
                        '{} == {}'.format(name, version), constraint=True)
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

            # Install the requirements.
            # TODO: This is where it fails for zc.recipe.egg.
            requirement_set.install(install_options=None)

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
