"""Add the UniSHARP node to Nuke's Nodes menu."""
import nuke

if getattr(nuke, "NUKE_VERSION_MAJOR", 0) >= 17:
    import unisharp_nuke

    unisharp_nuke.register_menu()
