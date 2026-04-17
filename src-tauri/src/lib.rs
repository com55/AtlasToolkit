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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(PendingUpdate(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![fetch_update, apply_update])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
