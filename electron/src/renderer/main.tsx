import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ChatInputBar from './components/ChatInputBar'
import { I18nProvider } from './i18n'
import { ThemeProvider } from './theme'
import './styles/index.css'
import './styles/workSurface.css'

const workPreviewWindow = new URLSearchParams(window.location.search).get('previewWindow') === '1'
const nativeTitlebarWindow = new URLSearchParams(window.location.search).get('mainWindow') === '1'
const chatInputWindow = new URLSearchParams(window.location.search).get('chatInput') === '1'
document.documentElement.classList.toggle('work-preview-window', workPreviewWindow)
document.body.classList.toggle('work-preview-window', workPreviewWindow)
document.documentElement.classList.toggle('native-titlebar-window', nativeTitlebarWindow)
document.body.classList.toggle('native-titlebar-window', nativeTitlebarWindow)
document.documentElement.classList.toggle('chat-input-window', chatInputWindow)
document.body.classList.toggle('chat-input-window', chatInputWindow)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ThemeProvider>
    <I18nProvider>{chatInputWindow ? <ChatInputBar /> : <App />}</I18nProvider>
  </ThemeProvider>,
)
