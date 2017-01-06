from pip import parseopts
from pip._vendor.packaging.utils import canonicalize_name
from pip.commands import commands_dict
from pip.download import PipSession
from pip.exceptions import PipError
from pip.index import FormatControl
from pip.index import PackageFinder
from pip.locations import distutils_scheme
from pip.req import InstallRequirement
from pip.req import RequirementSet
from pip.utils import get_installed_version
from pip.utils.build import BuildDirectory
from pip.wheel import WheelBuilder
from pip.wheel import WheelCache

import locale
import logging
import operator
import os
import sys
import tempfile


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


def pip_main(specs, versions):
    """Our variant of the pip.main function.

    We only support the install command, not any others.
    Well, maybe download or uninstall or wheel are nice too.
    But for now focus on install.

    We could maybe also import pip.main and call it.  But a function as
    bridge between buildout and pip seems good.
    """
    # pip install --help
    # I took some interesting options from pip 9.0.1.
    pip_args = [
        # Command:
        'install',

        # Install a project in editable mode.
        # --editable <path/url>

        # Install from the given requirements file.
        # Could be different way of specifying specs.
        # --requirement <file>

        # Install packages into <dir>.
        # --target <shared eggs dir?>

        # Download packages into <dir> instead of installing them.
        # --download <download cache?>

        # Directory to check out editable projects into.
        # Maybe something for mr.developer?
        # --src <dir>

        # Upgrade all specified packages to the newest available
        # version. The handling of dependencies depends on the
        # upgrade-strategy used.
        # -U, --upgrade

        # Determines how dependency upgrading should be handled.
        #
        # "eager" - dependencies are upgraded regardless of whether the
        # currently installed version satisfies the requirements of the
        # upgraded package(s).
        #
        # "only-if-needed" - are upgraded only when they do not satisfy the
        # requirements of the upgraded package(s).
        #
        # --upgrade-strategy <upgrade_strategy>

        # Installation prefix where lib, bin and other top-level folders
        # are placed.
        # --prefix <TODO buildout dir>

        # Compile py files to pyc.
        '--compile',

        # Do not use binary packages. Can be supplied multiple times, and
        # each time adds to the existing value. Accepts either :all: to
        # disable all binary packages, :none: to empty the set, or one or
        # more package names with commas between them. Note that some
        # packages are tricky to compile and may fail to install when this
        # option is used on them.
        '--no-binary', 'zc.recipe.egg',  # Maybe use canonicalize_name.
        # We may need to make this a config option in buildout.

        # Do not use source packages. Can be supplied multiple times, and
        # each time adds to the existing value. Accepts either :all: to
        # disable all source packages, :none: to empty the set, or one or
        # more package names with commas between them. Packages without
        # binary distributions will fail to install when this option is
        # used on them.
        # --only-binary <format_control>
        # We may need to make this a config option in buildout.

        # Include pre-release and development versions. By default, pip
        # only finds stable versions.
        # --pre
        # TODO Pass the prefer-final option from buildout.

        # Base URL of Python Package Index
        # (default https://pypi.python.org/simple).
        # --index-url <url>
        # TODO Pass the index option from buildout.

        # Extra URLs of package indexes to use in addition to --index-url.
        # Should follow the same rules as --index-url.
        # --extra-index-url <url>

        # Ignore package index (only looking at --find-links URLs instead).
        # --no-index
        # TODO: Maybe for offline buildout mode?

        # If a url or path to an html file, then parse for links to
        # archives. If a local path or file:// url that's a directory, then
        # look for archives in the directory listing.
        # --find-links <url>
        # TODO: Pass the find-links option from buildout

        # Give more output.  Option is additive, and can be used up to 3
        # times.
        # --verbose

        # Give less output. Option is additive, and can be used up to 3
        # times (corresponding to WARNING, ERROR, and CRITICAL logging
        # levels).
        # --quiet
        # TODO: We probably want to give this option once by default.

        # Set the socket timeout (default 15 seconds).
        # --timeout <sec>
        # TODO: pass timeout commandline argument from buildout.

        # Store the cache data in <dir>.
        # --cache-dir <dir>
        # I think this is only the wheel cache.

        # Don't periodically check PyPI to determine whether a new version
        # of pip is available for download.
        '--disable-pip-version-check',
    ]

    with tempfile.NamedTemporaryFile() as constraints_file:
        # Get version constraints.
        if versions is not None:
            for name, version in versions.items():
                if 'dev' in version:
                    # Could not find a version that satisfies the requirement
                    # zc.buildout==>=2.6.0.dev0
                    logger.warn('Ignoring dev constraint %s = %s',
                                name, version)
                    continue
                # Collecting zc.recipe.egg==>=2.0.0a3 fails for me, even
                # when it is already installed as dev version.  Pip says it
                # can't find it in a list that does actually contain it...
                if '>=' in version:
                    logger.warn('Ignoring ">=" constraint %s = %s',
                                name, version)
                    continue
                constraints_file.write('{0}=={1}\n'.format(name, version))
        constraints_file.seek(0)

        # Constrain versions using the given constraints file.
        pip_args.extend(['--constraint', constraints_file.name])
        # Add the requiremed specifications.
        pip_args.extend(specs)
        try:
            cmd_name, cmd_args = parseopts(pip_args)
        except PipError as exc:
            sys.stderr.write("ERROR: %s" % exc)
            sys.stderr.write(os.linesep)
            sys.exit(1)

        # Needed for locale.getpreferredencoding(False) to work
        # in pip.utils.encoding.auto_decode
        try:
            locale.setlocale(locale.LC_ALL, '')
        except locale.Error as e:
            # setlocale can apparently crash if locale are uninitialized
            logger.debug("Ignoring error %s when setting locale", e)
        command = commands_dict[cmd_name](isolated=False)
        return command.main(cmd_args)


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
            src_dir = 'src'
            download_dir = 'download'
            for folder in (src_dir, download_dir):
                if not os.path.exists(folder):
                    os.mkdir(folder)
                    logger.info('Created directory %s', folder)

            requirement_set = RequirementSet(
                build_dir=build_dir,
                src_dir=src_dir,
                download_dir=download_dir,
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
