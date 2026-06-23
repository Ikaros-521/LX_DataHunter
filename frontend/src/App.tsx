import { Routes, Route } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import Dashboard from './pages/Dashboard';
import CreatorList from './pages/CreatorList';
import CreatorDetail from './pages/CreatorDetail';
import CrawlerConsole from './pages/CrawlerConsole';
import ContentSearch from './pages/ContentSearch';
import CredentialManager from './pages/CredentialManager';
import ScheduleManager from './pages/ScheduleManager';

export default function App() {
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/creators" element={<CreatorList />} />
        <Route path="/creators/:id" element={<CreatorDetail />} />
        <Route path="/crawler" element={<CrawlerConsole />} />
        <Route path="/search" element={<ContentSearch />} />
        <Route path="/credentials" element={<CredentialManager />} />
        <Route path="/schedules" element={<ScheduleManager />} />
      </Routes>
    </AppLayout>
  );
}
