import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { useAuthStore } from "./stores/auth"
import { useThemeStore } from "./stores/theme"
import { Navbar } from "./components/layout/Navbar"
import { Sidebar } from "./components/layout/Sidebar"
import { CyberpunkOverlay } from "./components/layout/CyberpunkOverlay"
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
import { LessonsReviewPage } from "./pages/LessonsReview"
import { KnowledgeBasePage } from "./pages/KnowledgeBase"
import { MermaidViewerPage } from "./pages/MermaidViewer"
import { ProjectsPage } from "./pages/Projects"
import { ProjectDetailPage } from "./pages/ProjectDetail"
import { ProjectStoryBoardPage } from "./pages/ProjectStoryBoard"
import { BoardPreviewPage } from "./pages/BoardPreview"
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
      <Sidebar />
      {/* Sidebar is position:fixed (width 180); main content sits to its
          right via marginLeft — keep this in sync with Sidebar.tsx. */}
      <div style={{ marginLeft: 180, minWidth: 0 }}>{children}</div>
    </div>
  )
}

function App() {
  const theme = useThemeStore((s) => s.theme)
  const mode = useThemeStore((s) => s.mode)

  return (
    <div data-theme={theme} data-mode={mode}>
      <CyberpunkOverlay />
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
                    <Route path="/projects" element={<ProjectsPage />} />
                    <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
                    <Route path="/request/:requestId" element={<RequestDetailPage />} />
                    <Route path="/stories/project/:projectId" element={<ProjectStoryBoardPage />} />
                    <Route path="/stories/:requestId" element={<StoryBoardPage />} />
                    <Route path="/knowledge" element={<KnowledgeBasePage />} />
                    <Route path="/prompts" element={<PromptStudioPage />} />
                    <Route path="/history" element={<HistoryPage />} />
                    <Route path="/releases" element={<ReleasesPage />} />
                    <Route path="/team" element={<TeamStatusPage />} />
                    <Route path="/cost" element={<CostDashboardPage />} />
                    <Route path="/diagrams" element={<MermaidViewerPage />} />
                    <Route path="/users" element={<UserManagementPage />} />
                    <Route path="/lessons" element={<LessonsReviewPage />} />
                    {/* Standalone preview page — pick your click-to-drill behavior
                        before we wire the real Build Board / Command Center. */}
                    <Route path="/preview/board" element={<BoardPreviewPage />} />
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
