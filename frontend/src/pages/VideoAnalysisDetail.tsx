import { useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Descriptions, Progress, Space, Tabs, Tag, Tooltip, message, Alert } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import {
  useAnalysisDetail, useRetryAnalysis, useDeleteAnalysis,
} from '../api/queries';
import useAnalysisProgress from '../hooks/useAnalysisProgress';
import PlatformTag from '../components/PlatformTag';

function fmt(seconds: number) {
  const t = Math.floor(seconds);
  const m = Math.floor(t / 60), s = t % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function VideoAnalysisDetail() {
  useAnalysisProgress();
  const { id } = useParams();
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement>(null);
  const { data: a, isLoading } = useAnalysisDetail(id!);
  const retryMut = useRetryAnalysis();
  const deleteMut = useDeleteAnalysis();

  if (isLoading || !a) return <div>加载中…</div>;

  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) { v.currentTime = t; v.play(); }
  };

  const inProgress = ['pending', 'extracting', 'transcribing', 'analyzing'].includes(a.status);

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/analysis')}>返回</Button>
        <Tooltip title="重跑（默认复用 ASR）">
          <Button icon={<ReloadOutlined />}
            onClick={() => retryMut.mutate({ id: a.id }, { onSuccess: () => message.success('已重新入队') })} />
        </Tooltip>
        <Button danger icon={<DeleteOutlined />}
          onClick={() => deleteMut.mutate(a.id, { onSuccess: () => { message.success('已删除'); navigate('/analysis'); } })} />
      </Space>

      <Descriptions size="small" column={3} bordered style={{ marginBottom: 16 }}
        title={<Space>{a.title || '(无标题)'} <PlatformTag platform={a.platform} /></Space>}>
        <Descriptions.Item label="状态"><Tag>{a.stage || a.status}</Tag></Descriptions.Item>
        <Descriptions.Item label="进度"><Progress percent={Math.round(a.progress)} size="small" /></Descriptions.Item>
        <Descriptions.Item label="ASR">{a.asr_provider || '-'}{a.language ? ` / ${a.language}` : ''}</Descriptions.Item>
      </Descriptions>

      {a.status === 'failed' && <Alert type="error" showIcon style={{ marginBottom: 16 }} message="分析失败" description={a.error_message} />}

      {inProgress ? (
        <Alert type="info" showIcon message={`分析进行中：${a.stage || a.status}`} description={<Progress percent={Math.round(a.progress)} />} />
      ) : (
        <Tabs
          defaultActiveKey={(a.enabled_tasks && a.enabled_tasks[0]) || 'summary'}
          items={[
            ...(a.enabled_tasks?.includes('summary') ? [{
              key: 'summary', label: '内容总结',
              children: <ReactMarkdown>{a.content_summary || '（空）'}</ReactMarkdown>,
            }] : []),
            ...(a.enabled_tasks?.includes('script') ? [{
              key: 'script', label: '脚本与制作手法',
              children: <ReactMarkdown>{a.script_analysis || '（空）'}</ReactMarkdown>,
            }] : []),
            ...(a.enabled_tasks?.includes('knowledge') ? [{
              key: 'knowledge', label: '视频知识总结',
              children: <ReactMarkdown>{a.knowledge_summary || '（空）'}</ReactMarkdown>,
            }] : []),
            { key: 'transcript', label: `逐字稿 (${a.asr_segments.length})`, children: (
              <div>
                <video ref={videoRef} controls
                  src={`/api/v1/downloads/${a.download_task_id}/file`}
                  style={{ maxWidth: 480, width: '100%', marginBottom: 16, background: '#000' }} />
                <div style={{ maxHeight: '60vh', overflow: 'auto', fontFamily: 'monospace' }}>
                  {a.asr_segments.map((seg, i) => (
                    <div key={i} style={{ padding: '4px 0', cursor: 'pointer', borderBottom: '1px solid #f0f0f0' }}
                      onClick={() => seek(seg.start)}>
                      <span style={{ color: '#1677ff', marginRight: 8 }}>[{fmt(seg.start)}]</span>
                      {seg.text}
                    </div>
                  ))}
                  {a.asr_segments.length === 0 && <div>（无逐字稿）</div>}
                </div>
              </div>
            ) },
          ]}
        />
      )}
    </div>
  );
}
