'use strict'
// The sidebar's only way to the app: a few named calls, and news of changes. The server's pages
// get no preload at all, so nothing they run can reach these.

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('xrnhub', {
  state: () => ipcRenderer.invoke('xrnhub:state'),
  sheet: rect => ipcRenderer.invoke('xrnhub:sheet', rect),
  open: id => ipcRenderer.invoke('xrnhub:open', id),
  setup: form => ipcRenderer.invoke('xrnhub:setup', form),
  signIn: form => ipcRenderer.invoke('xrnhub:sign-in', form),
  retry: () => ipcRenderer.invoke('xrnhub:retry'),
  panel: name => ipcRenderer.invoke('xrnhub:panel', name),
  signOut: () => ipcRenderer.invoke('xrnhub:sign-out'),
  /** Calls `listener` with each new state; returns the way to stop. */
  onChange: listener => {
    const wrapped = (_event, state) => listener(state)
    ipcRenderer.on('xrnhub:changed', wrapped)
    return () => ipcRenderer.removeListener('xrnhub:changed', wrapped)
  }
})
