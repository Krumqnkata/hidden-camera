/**
 * Hidden Camera — приемник за клипове в Google Drive (Google Apps Script).
 *
 * НЕ копирайте този файл ръчно — пуснете:
 *     .venv\Scripts\python main.py --setup-script
 * Той генерира secrets\Code.gs с ВАШИЯ таен ключ и ви води стъпка по стъпка.
 *
 * Deploy -> New deployment -> Web app
 *     Execute as:      Me
 *     Who has access:  Anyone
 */

const SECRET = '__HCAM_SECRET__';   // таен ключ — без него никой не може да качва
const FOLDER_NAME = 'Hidden Camera'; // създава се автоматично в My Drive
const FOLDER_ID = '';                // ИЛИ ID на ваша съществуваща папка (по желание)

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.secret !== SECRET) return out_({ ok: false, error: 'bad secret' });

    const root = root_();
    if (req.action === 'ping') return out_({ ok: true, folder: root.getUrl() });

    const folder = req.date ? child_(root, String(req.date)) : root;
    const existing = folder.getFilesByName(req.name);
    if (existing.hasNext()) {
      const f = existing.next();
      return out_({ ok: true, id: f.getId(), url: f.getUrl(), existed: true });
    }
    const bytes = Utilities.base64Decode(req.data);
    const blob = Utilities.newBlob(bytes, req.mimeType || 'video/mp4', req.name);
    const file = folder.createFile(blob);
    return out_({ ok: true, id: file.getId(), url: file.getUrl() });
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  }
}

function doGet() {
  return out_({ ok: true, service: 'hidden-camera' });
}

/** Пуснете веднъж от редактора (Run), за да дадете права и да се създаде папката. */
function setup() {
  Logger.log('Папка: ' + root_().getUrl());
}

function root_() {
  if (FOLDER_ID) return DriveApp.getFolderById(FOLDER_ID);
  const props = PropertiesService.getScriptProperties();
  const saved = props.getProperty('ROOT_ID');
  if (saved) {
    try {
      const f = DriveApp.getFolderById(saved);
      if (!f.isTrashed()) return f;
    } catch (e) { /* изтрита — създай нова */ }
  }
  const it = DriveApp.getRootFolder().getFoldersByName(FOLDER_NAME);
  const folder = it.hasNext() ? it.next() : DriveApp.getRootFolder().createFolder(FOLDER_NAME);
  props.setProperty('ROOT_ID', folder.getId());
  return folder;
}

function child_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
