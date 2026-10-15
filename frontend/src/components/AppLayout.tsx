import { useEffect, useMemo, useRef, useState } from 'react';
import { Breadcrumb, Layout, Menu, Typography } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined, UserOutlined, SearchOutlined,
  ThunderboltOutlined, KeyOutlined, ClockCircleOutlined, FireOutlined,
  DownloadOutlined, VideoCameraOutlined, FundViewOutlined,
  RocketOutlined, DatabaseOutlined, BarChartOutlined, SettingOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';

const { Sider, Content, Header } = Layout;

type MenuLeaf = { key: string; icon: React.ReactNode; label: string };
type MenuNode = MenuLeaf | { key: string; icon: React.ReactNode; label: string; children: MenuLeaf[] };

// 按「数据工作流」分层：配置/执行采集 → 浏览数据 → 分析洞察 → 系统配置
const menuConfig: MenuNode[] = [
  { key: '/', icon: <DashboardOutlined />, label: '概览' },
  {
    key: 'collect', icon: <RocketOutlined />, label: '数据采集', children: [
      { key: '/creators', icon: <UserOutlined />, label: '创作者管理' },
      { key: '/crawler', icon: <ThunderboltOutlined />, label: '爬虫控制台' },
      { key: '/hot', icon: <FireOutlined />, label: '热门爬取' },
      { key: '/schedules', icon: <ClockCircleOutlined />, label: '定时任务' },
    ],
  },
  {
    key: 'data', icon: <DatabaseOutlined />, label: '数据中心', children: [
      { key: '/search', icon: <SearchOutlined />, label: '内容检索' },
      { key: '/downloads', icon: <DownloadOutlined />, label: '下载管理' },
    ],
  },
  {
    key: 'insight', icon: <BarChartOutlined />, label: '分析洞察', children: [
      { key: '/analysis', icon: <VideoCameraOutlined />, label: '视频分析' },
      { key: '/research', icon: <FundViewOutlined />, label: '竞品调研' },
    ],
  },
  {
    key: 'system', icon: <SettingOutlined />, label: '系统设置', children: [
      { key: '/credentials', icon: <KeyOutlined />, label: '凭证管理' },
    ],
  },
];

// 展平路由表：叶子路由 → { 页面名, 所属分组 }，供菜单选中态与面包屑共用
const flatRoutes = menuConfig.flatMap(node =>
  'children' in node
    ? node.children.map(child => ({ key: child.key, label: child.label, groupKey: node.key, groupLabel: node.label }))
    : [{ key: node.key, label: node.label, groupKey: undefined as string | undefined, groupLabel: undefined as string | undefined }],
);

function useActiveRoute() {
  const location = useLocation();
  const seg = location.pathname.split('/')[1];
  const leafKey = seg === '' ? '/' : `/${seg}`;
  return flatRoutes.find(r => r.key === leafKey) ?? flatRoutes[0];
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const active = useActiveRoute();

  // 受控展开：挂载和路由切换时自动展开所属分组；用户手动收起当前分组时不强行展开
  const [openKeys, setOpenKeys] = useState<string[]>(active.groupKey ? [active.groupKey] : []);
  const prevGroupRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (active.groupKey && active.groupKey !== prevGroupRef.current) {
      setOpenKeys(prev => (prev.includes(active.groupKey!) ? prev : [...prev, active.groupKey!]));
    }
    prevGroupRef.current = active.groupKey;
  }, [active.groupKey]);

  const onOpenChange: MenuProps['onOpenChange'] = keys => setOpenKeys(keys as string[]);

  const breadcrumbItems = useMemo(() => {
    const items: { title: string }[] = [];
    if (active.groupLabel) items.push({ title: active.groupLabel });
    items.push({ title: active.label });
    return items;
  }, [active]);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth="0">
        <div style={{ height: 32, margin: 16, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Typography.Text style={{ color: '#fff', fontSize: 18, fontWeight: 'bold' }}>
            LX_DataHunter
          </Typography.Text>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[active.key]}
          openKeys={openKeys}
          onOpenChange={onOpenChange}
          items={menuConfig}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 2, height: 'auto', minHeight: 64 }}>
          <Breadcrumb items={breadcrumbItems} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            {active.label}
          </Typography.Title>
        </Header>
        <Content style={{ margin: 24, minHeight: 280 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
