use tauri::Manager;
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::Mutex;

struct PendingUpdate(Mutex<Option<tauri_plugin_updater::Update>>);

#[tauri::command]
async fn fetch_update(
    app: tauri::AppHandle,
    state: tauri::State<'_, PendingUpdate>,
) -> Result<Option<serde_json::Value>, String> {
    let updater = app
        .updater_builder()
        .build()
        .map_err(|e| e.to_string())?;

    match updater.check().await {
        Ok(Some(update)) => {
            let info = serde_json::json!({
                "version": update.version,
                "body": update.body,
            });
            *state.0.lock().await = Some(update);
            Ok(Some(info))
        }
        Ok(None) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
async fn apply_update(
    app: tauri::AppHandle,
    state: tauri::State<'_, PendingUpdate>,
) -> Result<(), String> {
    let update = state
        .0
        .lock()
        .await
        .take()
        .ok_or_else(|| "No pending update".to_string())?;

    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;

    app.restart();
}

fn pick_atlas_arg<I>(args: I) -> Option<String>
where
    I: IntoIterator<Item = String>,
{
    args.into_iter()
        .skip(1)
        .find(|a| a.to_lowercase().ends_with(".atlas"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(PendingUpdate(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            fetch_update,
            apply_update,
            get_startup_file
        ])
        .setup(|app| {
            let startup = pick_atlas_arg(std::env::args());
            app.manage(StartupFile(std::sync::Mutex::new(startup)));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

struct StartupFile(std::sync::Mutex<Option<String>>);

#[tauri::command]
fn get_startup_file(state: tauri::State<'_, StartupFile>) -> Option<String> {
    state.0.lock().ok().and_then(|mut guard| guard.take())
}
