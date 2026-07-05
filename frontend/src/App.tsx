// App shell + routing. Public routes: login / register / verify. Everything else is
// gated behind ProtectedRoute (redirects to /login when unauthenticated).

import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import History from "./pages/History";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Review from "./pages/Review";
import Upload from "./pages/Upload";
import Verify from "./pages/Verify";

function ProtectedRoute({ children }: { children: JSX.Element }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <div className="app-main">Loading…</div>;
  if (status === "anon") return <Navigate to="/login" replace state={{ from: location }} />;
  return children;
}

function AppShell({ children }: { children: JSX.Element }) {
  const { user, logout } = useAuth();
  return (
    <>
      <nav className="app-nav">
        <strong>GovFill</strong>
        <Link to="/">Dashboard</Link>
        <Link to="/upload">Upload</Link>
        <Link to="/review">Review</Link>
        <Link to="/history">History</Link>
        <span className="spacer" />
        <span style={{ fontSize: 13, color: "#666" }}>{user?.email}</span>
        <button className="link" onClick={() => void logout()}>
          Log out
        </button>
      </nav>
      <main className="app-main">{children}</main>
    </>
  );
}

function Dashboard() {
  return (
    <div>
      <h1>Dashboard</h1>
      <p>You're signed in. Document upload and form filling arrive in the next phases.</p>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/verify" element={<Verify />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AppShell>
              <Dashboard />
            </AppShell>
          </ProtectedRoute>
        }
      />
      <Route
        path="/upload"
        element={
          <ProtectedRoute>
            <AppShell>
              <Upload />
            </AppShell>
          </ProtectedRoute>
        }
      />
      <Route
        path="/review"
        element={
          <ProtectedRoute>
            <AppShell>
              <Review />
            </AppShell>
          </ProtectedRoute>
        }
      />
      <Route
        path="/history"
        element={
          <ProtectedRoute>
            <AppShell>
              <History />
            </AppShell>
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
