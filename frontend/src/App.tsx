import { AppRouter } from './app/router'
import { AuthProvider } from './features/auth/AuthContext'
import { ThemeProvider } from './features/theme/ThemeContext'
import { ToastProvider } from './components/ui/ToastProvider'

export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <AppRouter />
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  )
}
