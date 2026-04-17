# Bash completions for control-ofc-gui
_control_ofc_gui() {
    local cur opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    opts="--socket --demo --help"

    if [[ ${cur} == -* ]]; then
        COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
    fi
}
complete -F _control_ofc_gui control-ofc-gui
