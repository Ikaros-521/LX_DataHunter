import { Tag } from 'antd';

const platformColors: Record<string, string> = {
  douyin: '#ff0050',
  bilibili: '#fb7299',
  kuaishou: '#ff4906',
  xiaohongshu: '#fe2c55',
};

const platformNames: Record<string, string> = {
  douyin: '抖音',
  bilibili: 'B站',
  kuaishou: '快手',
  xiaohongshu: '小红书',
};

export default function PlatformTag({ platform }: { platform: string }) {
  return (
    <Tag color={platformColors[platform] || '#999'}>
      {platformNames[platform] || platform}
    </Tag>
  );
}
