import threading
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from pydantic import BaseModel
from typing import Optional
import os
import sys
import asyncio
from main import PROGRESS_STATE

import main

app = FastAPI()

class DownloadRequest(BaseModel):
    entity: str
    topic_id: Optional[int] = None
    output_dir: str = "~/Downloads/TelegramDownload/DiscreteMathematics"
    limit: int = 0  # 0 means download all files

def run_download_task(request_data: DownloadRequest):
    global PROGRESS_STATE
    PROGRESS_STATE["logs"].clear()
    PROGRESS_STATE["errors"].clear()
    PROGRESS_STATE["status"] = "starting"
    
    expanded_output = request_data.output_dir.replace("~", os.path.expanduser("~"))
    
    # We must strictly isolate the asyncio loop since FastAPI has its own
    # and Telethon's sync magic requires exclusive control over the thread's loop.
    import threading
    def thread_worker():
        try:
            # Create a completely fresh event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Recreate the client globally inside this thread so teleporting works
            from telethon import TelegramClient
            import os
            from dotenv import load_dotenv
            load_dotenv()
            
            API_ID = os.getenv("TELEGRAM_API_ID")
            API_HASH = os.getenv("TELEGRAM_API_HASH")
            main.telegram_client = TelegramClient("session_name", API_ID, API_HASH, loop=loop)
            
            main.download_files_from_entity(
                entity_identifier=request_data.entity,
                file_type=None,
                save_directory=expanded_output,
                message_limit=request_data.limit,
                topic_id=request_data.topic_id
            )
            PROGRESS_STATE["status"] = "finished"
        except Exception as e:
            PROGRESS_STATE["status"] = "error"
            import traceback
            import logging
            logging.error(f"Download task crashed: {e}")
            logging.error(traceback.format_exc())

    t = threading.Thread(target=thread_worker)
    t.start()

@app.post("/start")
def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    if PROGRESS_STATE["status"] in ["downloading", "starting"]:
        return {"error": "A download is already in progress"}
    background_tasks.add_task(run_download_task, req)
    return {"message": "Download started!"}

@app.get("/status")
def get_status():
    return JSONResponse(content=PROGRESS_STATE)

@app.get("/", response_class=HTMLResponse)
def index_page():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram Downloader UI</title>
        <style>
            body { font-family: -apple-system, system-ui, sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; background: #0f172a; color: #f1f5f9; }
            h1 { font-weight: 600; }
            .card { background: #1e293b; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #334155; }
            input, select, button { width: 100%; padding: 10px; margin-bottom: 12px; background: #334155; border: 1px solid #475569; color: white; border-radius: 4px; box-sizing: border-box; }
            button { background: #3b82f6; color: white; border: none; cursor: pointer; font-weight: bold; }
            button:hover { background: #2563eb; }
            .progress-container { width: 100%; background: #334155; border-radius: 4px; overflow: hidden; margin-top: 10px; height: 24px; position: relative;}
            .progress-bar { height: 100%; background: #22c55e; width: 0%; transition: width 0.3s; }
            .progress-text { position: absolute; width: 100%; text-align: center; top: 2px; font-size: 14px; font-weight: bold; text-shadow: 1px 1px 2px black; }
            .log-box { font-family: monospace; font-size: 13px; background: #000; padding: 10px; height: 150px; overflow-y: auto; border-radius: 4px; color: #a3e635; }
            .error-box { font-family: monospace; font-size: 13px; background: #450a0a; padding: 10px; height: 100px; overflow-y: auto; border-radius: 4px; color: #fca5a5; margin-top: 10px;}
        </style>
    </head>
    <body>
        <h1>🗂️ Telegram Downloader</h1>
        
        <div class="card">
            <h3>Start Download</h3>
            <input type="text" id="entity" placeholder="Channel / Group ID (e.g. -1003468527316)" value="-1003468527316">
            <input type="number" id="topic" placeholder="Topic ID (optional)" value="1931">
            <input type="text" id="output" placeholder="Output Directory" value="~/Downloads/TelegramDownload/DiscreteMathematics">
            <input type="number" id="limit" placeholder="Message Limit (0 = ALL)" value="0">
            <button onclick="startDownload()">Start Web Download ⬇️</button>
            <p id="startMsg" style="font-size: 14px; color: yellow;"></p>
        </div>

        <div class="card">
            <h3>Status: <span id="statusBadge" style="color: #60a5fa;">idle</span></h3>
            <p style="font-size: 14px;"><strong>Downloading:</strong> <span id="currentFile">None</span></p>
            <div class="progress-container">
                <div class="progress-bar" id="pbar"></div>
                <div class="progress-text" id="ptext">0%</div>
            </div>
            <p style="font-size: 14px; text-align: right; margin-top: 5px;">Speed: <strong id="speed">0 B/s</strong></p>
        </div>

        <div class="card">
            <h3>System Logs</h3>
            <div class="log-box" id="logs"></div>
            <h3 style="margin-top: 15px; color: #f87171;">Errors (if any)</h3>
            <div class="error-box" id="errors"></div>
        </div>

        <script>
            function humanSize(bytes) {
                if(bytes == 0) return '0 B';
                var k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'], i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
            }

            async function startDownload() {
                const req = {
                    entity: document.getElementById('entity').value,
                    topic_id: document.getElementById('topic').value ? parseInt(document.getElementById('topic').value) : null,
                    output_dir: document.getElementById('output').value,
                    limit: parseInt(document.getElementById('limit').value)
                };
                const res = await fetch('/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(req)
                });
                const data = await res.json();
                document.getElementById('startMsg').innerText = data.error || data.message;
                setTimeout(() => document.getElementById('startMsg').innerText = "", 3000);
            }

            async function pollStatus() {
                try {
                    const res = await fetch('/status');
                    const state = await res.json();
                    
                    document.getElementById('statusBadge').innerText = state.status;
                    document.getElementById('currentFile').innerText = state.current_file || "None";
                    document.getElementById('speed').innerText = state.speed;

                    let percent = 0;
                    if(state.total_bytes > 0) {
                        percent = (state.downloaded_bytes / state.total_bytes) * 100;
                    }
                    
                    document.getElementById('pbar').style.width = percent + "%";
                    document.getElementById('ptext').innerText = `${percent.toFixed(1)}% (${humanSize(state.downloaded_bytes)} / ${humanSize(state.total_bytes)})`;

                    // Updates Logs
                    const logsEl = document.getElementById('logs');
                    const errEl = document.getElementById('errors');
                    
                    if(document.lastLogsCount !== state.logs.length) {
                        logsEl.innerHTML = state.logs.slice(-30).join('<br/>');
                        logsEl.scrollTop = logsEl.scrollHeight;
                        document.lastLogsCount = state.logs.length;
                    }

                    if(document.lastErrCount !== state.errors.length) {
                        errEl.innerHTML = state.errors.join('<br/>');
                        errEl.scrollTop = errEl.scrollHeight;
                        document.lastErrCount = state.errors.length;
                    }

                } catch(e) {
                    console.log("Polling error");
                }
            }

            setInterval(pollStatus, 1000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run("web_ui:app", host="0.0.0.0", port=8000, reload=False)