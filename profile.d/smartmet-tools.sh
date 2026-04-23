# Auto-sourced by login shells (Bash / Zsh) from /etc/profile.d/.
# Brings the legacy bstat / bchart / burls / bstatus / bkeys functions
# into interactive sessions. The file is safe to source in a non-interactive
# shell but does nothing useful there.

if [ -n "$PS1" ] || [ -n "$BASH_INTERACTIVE_SHELL" ]; then
    if [ -r /usr/share/smartmet/bstat.sh ]; then
        # shellcheck source=/usr/share/smartmet/bstat.sh
        . /usr/share/smartmet/bstat.sh
    fi
fi
