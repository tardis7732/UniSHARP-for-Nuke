"""Load saved UniSHARP callback functions in GUI and command-line Nuke."""
import nuke

if getattr(nuke, "NUKE_VERSION_MAJOR", 0) >= 17:
    import unisharp_nuke  # noqa: F401
