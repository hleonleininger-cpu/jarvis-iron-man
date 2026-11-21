// Unter Windows kein Konsolenfenster im fertigen Bau.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    jarvis_hud_lib::run()
}
