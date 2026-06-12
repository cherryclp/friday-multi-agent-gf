const { app, BrowserWindow } = require('electron')
const { spawn } = require('child_process')
const path = require('path')

let mainWindow
let pythonProcess

function startPythonBackend() {
  // FIX: path was '../jarvis_server.py' which is wrong when main.js is in the project root.
  // Use __dirname to always resolve relative to this file.
  const serverPath = path.join(__dirname, 'jarvis_server.py')
  pythonProcess = spawn('python', [serverPath], { cwd: __dirname })

  pythonProcess.stdout.on('data', (data) => {
    console.log(`Python: ${data}`)
  })

  pythonProcess.stderr.on('data', (data) => {
    // stderr includes normal startup logs, only flag real errors
    const msg = String(data)
    if (!msg.includes('WARNING') && !msg.includes('Running on')) {
      console.error(`Python Error: ${data}`)
    }
  })

  pythonProcess.on('exit', (code) => {
    console.log(`Python backend exited with code ${code}`)
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 980,
    height: 700,
    frame: false,
    transparent: true,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      preload: path.join(__dirname, 'preload.js')
    },
    icon: path.join(__dirname, 'icon.png')
  })

  // FIX: loading index.html as a file:// URL causes fetch() to localhost:5000 to fail
  // in Electron's strict security context. Load directly from Flask instead.
  // We poll until Flask is ready before loading.
  const http = require('http')
  let attempts = 0
  const maxAttempts = 30

  function tryLoad() {
    attempts++
    http.get('http://localhost:5000/yap_poll', (res) => {
      // Flask is up — load from Flask so fetch() same-origin rules are satisfied
      // BUT since index.html does its own fetch to localhost:5000, loading as file is also fine.
      // Load as file for offline support; just wait for Flask to be ready first.
      mainWindow.loadFile(path.join(__dirname, 'index.html'))
    }).on('error', () => {
      if (attempts < maxAttempts) {
        setTimeout(tryLoad, 500)
      } else {
        console.error('Flask backend never started — loading UI anyway')
        mainWindow.loadFile(path.join(__dirname, 'index.html'))
      }
    })
  }

  // Give Python a moment to start its import chain before polling
  setTimeout(tryLoad, 2000)
}

app.whenReady().then(() => {
  startPythonBackend()
  createWindow()
})

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill()
  }
  app.quit()
})