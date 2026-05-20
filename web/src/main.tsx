import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App'
import Library from './routes/Library'
import VideoPage from './routes/Video'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<Library />} />
          <Route path="videos/:id" element={<VideoPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
