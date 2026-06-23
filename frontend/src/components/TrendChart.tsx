import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Card } from 'antd';

interface TrendChartProps {
  data: Array<{
    time: string;
    likes: number;
    comments: number;
    shares: number;
    plays: number;
  }>;
  loading?: boolean;
}

export default function TrendChart({ data, loading }: TrendChartProps) {
  return (
    <Card title="互动趋势" loading={loading}>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="time" />
          <YAxis />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="likes" stroke="#1677ff" name="点赞" />
          <Line type="monotone" dataKey="comments" stroke="#52c41a" name="评论" />
          <Line type="monotone" dataKey="shares" stroke="#fa8c16" name="转发" />
          <Line type="monotone" dataKey="plays" stroke="#722ed1" name="播放" />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}
