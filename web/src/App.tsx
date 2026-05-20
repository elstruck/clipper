import { Link, Outlet, useLocation } from 'react-router-dom'

export default function App() {
  const loc = useLocation()
  const onVideo = loc.pathname.startsWith('/videos/')
  return (
    <div className="app">
      <header className="header">
        <Link to="/"><h1>Marlin Clipper</h1></Link>
        {onVideo && <span className="crumb">/ video</span>}
        <div className="spacer" />
      </header>
      <Outlet />
    </div>
  )
}
