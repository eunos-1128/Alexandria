"""Where this copy of Alexandria is running, and what it can see.

Under Flatpak the application does not get the user's filesystem; it
gets the list of places named in the manifest (plus whatever the user
has since granted with Flatseal, and whatever arrives through a
portal). Alexandria cares because its central object is a *folder* —
the library root — and a folder the sandbox cannot see fails in ways
that look like bugs in the application rather than the packaging:

  * a PDF dropped from `~/Documents` when only
    `xdg-documents/Alexandria` is granted lands in `_on_drop`, fails
    `os.path.isfile`, and is reported as **"no PDFs found"**;
  * a catalogue whose `library_root` is outside the grant simply
    stays empty, with nothing said about why.

Both become answerable once the application knows the difference
between "that file is not there" and "that file is not mine to see".

`/.flatpak-info` is the canonical marker — GLib itself tests for it
when deciding whether to route through portals — and it is more than
a marker: it is the live sandbox configuration, including any
Flatseal override applied after installation, in an ini file we can
read without shelling out to `flatpak`:

    [Application]
    name=io.github.pemsley.Alexandria

    [Context]
    filesystems=xdg-documents/Alexandria:create;

Outside a sandbox every function here answers "no restriction", so
callers need no `if in_flatpak()` of their own.
"""

import configparser
import os

FLATPAK_INFO = "/.flatpak-info"

# Parsed `/.flatpak-info`, keyed by path. The file cannot change
# while the application runs — a Flatseal edit takes effect at the
# next launch — so one read is enough.
_info_cache = {}


def in_flatpak(info_path=FLATPAK_INFO):
    """True when running inside a Flatpak sandbox.

    The file test rather than `$FLATPAK_ID`: environment variables
    are inherited by anything we spawn (the embedded terminal, the
    MCP server) and can be unset, while the file is part of the
    sandbox itself."""
    return os.path.exists(info_path)


def _info(info_path=FLATPAK_INFO):
    """Parsed `/.flatpak-info`, or None outside a sandbox."""
    if info_path in _info_cache:
        return _info_cache[info_path]
    parsed = None
    if os.path.exists(info_path):
        cp = configparser.ConfigParser(interpolation=None)
        # Values are Flatpak's own, not ours: don't let a key we
        # have never heard of stop us reading the ones we want.
        try:
            cp.read(info_path)
            parsed = cp
        except Exception:
            parsed = None
    _info_cache[info_path] = parsed
    return parsed


def forget_info():
    """Drop the cached parse. For tests, which point the module at
    fixture files."""
    _info_cache.clear()


def app_id(info_path=FLATPAK_INFO):
    """The sandboxed application id, or '' when not sandboxed."""
    cp = _info(info_path)
    if cp is None:
        return ""
    try:
        return cp.get("Application", "name", fallback="") or ""
    except Exception:
        return ""


def granted_filesystems(info_path=FLATPAK_INFO):
    """The raw `[Context] filesystems=` tokens, e.g.

        ("xdg-documents/Alexandria:create",)

    Empty outside a sandbox, and empty for a sandbox granted
    nothing."""
    cp = _info(info_path)
    if cp is None:
        return ()
    try:
        raw = cp.get("Context", "filesystems", fallback="") or ""
    except Exception:
        return ()
    return tuple(t for t in (p.strip() for p in raw.split(";")) if t)


def _xdg_special(name):
    """`~/Documents` and friends, by GLib where it is available so a
    localised or relocated user-dirs setup is honoured."""
    fallbacks = {
        "desktop": "~/Desktop", "documents": "~/Documents",
        "download": "~/Downloads", "music": "~/Music",
        "pictures": "~/Pictures", "public-share": "~/Public",
        "templates": "~/Templates", "videos": "~/Videos",
    }
    try:
        from gi.repository import GLib
        key = {"desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
               "documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
               "download": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
               "music": GLib.UserDirectory.DIRECTORY_MUSIC,
               "pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
               "public-share": GLib.UserDirectory.DIRECTORY_PUBLIC_SHARE,
               "templates": GLib.UserDirectory.DIRECTORY_TEMPLATES,
               "videos": GLib.UserDirectory.DIRECTORY_VIDEOS}.get(name)
        if key is not None:
            got = GLib.get_user_special_dir(key)
            if got:
                return got
    except Exception:
        pass
    return os.path.expanduser(fallbacks.get(name, "~"))


def _expand_token(token):
    """One `filesystems=` entry to an absolute path prefix, or None
    when it grants no path we can name (`host-os`, a bare `!token`
    negation, anything unrecognised).

    Flatpak's spelling: an optional `:ro` / `:rw` / `:create` mode
    suffix, `xdg-<dir>` with an optional `/subpath`, `~/relative`,
    `home`, `host`, or an absolute path."""
    token = token.strip()
    if not token or token.startswith("!"):
        return None
    for mode in (":create", ":rw", ":ro"):
        if token.endswith(mode):
            token = token[:-len(mode)]
            break
    if not token:
        return None
    if token in ("host", "host-reset"):
        return "/"
    if token in ("host-os", "host-etc"):
        # Read-only system trees; nothing of the user's lives there.
        return None
    if token == "home":
        return os.path.expanduser("~")
    if token.startswith("xdg-run"):
        run = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/{}".format(
            os.getuid())
        return os.path.join(run, token[len("xdg-run"):].lstrip("/"))
    if token.startswith("xdg-"):
        name, _, sub = token[len("xdg-"):].partition("/")
        if name in ("config", "cache", "data", "state"):
            base = os.path.expanduser("~/." + (
                "local/share" if name == "data"
                else "local/state" if name == "state" else name))
        else:
            base = _xdg_special(name)
        return os.path.join(base, sub) if sub else base
    if token.startswith("~/"):
        return os.path.expanduser(token)
    if token.startswith("/"):
        return token
    return None


def granted_paths(info_path=FLATPAK_INFO):
    """Absolute path prefixes this sandbox can reach, expanded from
    `granted_filesystems`. Empty outside a sandbox — which means "no
    restriction", not "nothing": see `can_access`."""
    out = []
    for token in granted_filesystems(info_path):
        path = _expand_token(token)
        if path and path not in out:
            out.append(path)
    return out


def _under(path, prefix):
    if prefix == "/":
        return True
    path = os.path.normpath(path)
    prefix = os.path.normpath(prefix)
    return path == prefix or path.startswith(prefix + os.sep)


def can_access(path, info_path=FLATPAK_INFO):
    """Whether `path` is one this process could open if it existed.

    Always True outside a sandbox. Inside one, True when the path
    falls under a granted filesystem, under the document portal's
    mount (where portal-granted files appear), or under the
    application's own private directories — which are `~/.config`,
    `~/.local/share` and friends as seen from inside, redirected to
    `~/.var/app/<id>/` on the host.

    It answers about *permission*, not existence: a path that is
    granted but absent still returns True, which is what makes it
    useful for telling "not there" from "not allowed"."""
    if not path:
        return False
    cp = _info(info_path)
    if cp is None:
        return True
    home = os.path.expanduser("~")
    always = [
        os.path.join(home, ".config"), os.path.join(home, ".cache"),
        os.path.join(home, ".local", "share"),
        os.path.join(home, ".local", "state"),
        os.environ.get("XDG_RUNTIME_DIR") or "/run/user/{}".format(
            os.getuid()),
        "/tmp",
    ]
    for prefix in list(granted_paths(info_path)) + always:
        if _under(path, prefix):
            return True
    return False


def access_summary(info_path=FLATPAK_INFO):
    """One human sentence naming the folders the user can put a
    library in, or '' when there is no restriction to explain.

    For toasts and dialogs, where the honest message is "that file is
    outside what this Flatpak can open" rather than "no PDFs found".
    Paths are written with `~` because that is how the user thinks of
    them, and how Flatseal shows them."""
    if _info(info_path) is None:
        return ""
    paths = granted_paths(info_path)
    if not paths:
        return ("This Flatpak has not been granted access to any "
                "folder. Use Flatseal to give it one.")
    home = os.path.expanduser("~")
    pretty = []
    for p in paths:
        if p == "/":
            return ""          # full host access: nothing to warn about
        pretty.append("~" + p[len(home):] if p.startswith(home) else p)
    if len(pretty) == 1:
        return "This Flatpak can only open files in {}.".format(pretty[0])
    return "This Flatpak can only open files in {} and {}.".format(
        ", ".join(pretty[:-1]), pretty[-1])
