import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { useAuthStore } from "./stores/auth"
import { useThemeStore } from "./stores/theme"
import { Navbar } from "./components/layout/Navbar"
import { LoginPage } from "./pages/Login"
import { CommandCenterPage } from "./pages/CommandCenter"
import { RequestDetailPage } from "./pages/RequestDetail"
import { StoryBoardPage } from "./pages/StoryBoard"
import { PromptStudioPage } from "./pages/PromptStudio"
import { HistoryPage } from "./pages/History"
import { ReleasesPage } from "./pages/Releases"
import { TeamStatusPage } from "./pages/TeamStatus"
import { CostDashboardPage } from "./pages/CostDashboard"
import { UserManagementPage } from "./pages/UserManagement"
import "./themes.css"

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--bg-primary)",
        color: "var(--text-primary)",
        fontFamily: "var(--font)",
      }}
    >
      <Navbar />
      {children}
    </div>
  )
}

function App() {
  const theme = useThemeStore((s) => s.theme)
  const mode = useThemeStore((s) => s.mode)

  return (
    <div data-theme={theme} data-mode={mode}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <Layout>
                  <Routes>
                    <Route path="/" element={<CommandCenterPage />} />
                    <Route path="/request/:requestId" element={<RequestDetailPage />} />
                    <Route path="/stories/:requestId" element={<StoryBoardPage />} />
                    <Route path="/prompts" element={<PromptStudioPage />} />
                    <Route path="/history" element={<HistoryPage />} />
                    <Route path="/releases" element={<ReleasesPage />} />
                    <Route path="/team" element={<TeamStatusPage />} />
                    <Route path="/cost" element={<CostDashboardPage />} />
                    <Route path="/users" element={<UserManagementPage />} />
                  </Routes>
                </Layout>
              </RequireAuth>
            }
          />
        </Routes>
      </BrowserRouter>
    </div>
  )
}

export default App
