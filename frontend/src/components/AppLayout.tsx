import { Layout, Menu, Typography } from 'antd';
import {
  DashboardOutlined, UserOutlined, SearchOutlined,
  SettingOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';

const { Sider, Content, Header } = Layout;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '概览' },
  { key: '/creators', icon: <UserOutlined />, label: '创作者管理' },
  { key: '/crawler', icon: <ThunderboltOutlined />, label: '爬虫控制台' },
  { key: '/search', icon: <SearchOutlined />, label: '内容检索' },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth="0">
        <div style={{ height: 32, margin: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Typography.Text style={{ color: '#fff', fontSize: 18, fontWeight: 'bold' }}>
            FaPai Spider
          </Typography.Text>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname === '/' ? '/' : '/' + location.pathname.split('/')[1]]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
          <Typography.Title level={4} style={{ margin: '16px 0' }}>
            {menuItems.find(m => {
              const seg = location.pathname.split('/')[1];
              return m.key === '/' + seg || (seg === '' && m.key === '/');
            })?.label || '概览'}
          </Typography.Title>
        </Header>
        <Content style={{ margin: 24, minHeight: 280 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
