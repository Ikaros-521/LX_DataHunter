import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Row, Col, Card, Tabs, Tag, Button, Space, Select, Input, Spin, Typography,
  Empty, message,
} from 'antd';
import {
  ArrowLeftOutlined, ThunderboltOutlined, CopyOutlined, DownloadOutlined,
  SendOutlined, MessageOutlined, DeleteOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import {
  useAnalysisBatchDetail, useAnalysisDigest,
  type VideoAnalysis, type ChatMessage,
} from '../api/queries';
import { streamChat } from '../api/stream';
import PlatformTag from '../components/PlatformTag';

export default function AnalysisCompare() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // 从 URL 取 ids
  const ids = (searchParams.get('ids') || '')
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);

  const { data, isLoading } = useAnalysisBatchDetail(ids);
  const items: VideoAnalysis[] = data?.items || [];

  // 全局统一维度：对话与大总结共享
  const [task, setTask] = useState<'summary' | 'knowledge'>('summary');

  // 大总结（一次性，不落库）
  const digestMut = useAnalysisDigest();
  const [digestText, setDigestText] = useState('');

  // 对话（流式打字机，不落库）
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const msgListRef = useRef<HTMLDivElement>(null);

  // 切换维度时清空对话与大总结（上下文素材变了）
  const handleTaskChange = (v: 'summary' | 'knowledge') => {
    setTask(v);
    setChatMessages([]);
    setDigestText('');
  };

  // 流式中自动滚动到底部
  useEffect(() => {
    if (msgListRef.current) {
      msgListRef.current.scrollTop = msgListRef.current.scrollHeight;
    }
  }, [chatMessages]);

  // 组件卸载时中断进行中的流
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // ---- 大总结 ----
  const handleDigest = () => {
    digestMut.mutate(
      { analysis_ids: ids, task },
      {
        onSuccess: (d) => {
          setDigestText(d.digest);
          message.success(`已生成大总结（聚合 ${d.source_count} 条）`);
        },
        onError: () => message.error('大总结生成失败'),
      },
    );
  };
  const copyText = (text: string, label = '内容') => {
    if (!text) { message.warning(`${label}为空`); return; }
    navigator.clipboard.writeText(text).then(
      () => message.success(`已复制${label}`),
      () => message.error('复制失败'),
    );
  };
  const downloadText = (text: string, name: string) => {
    if (!text) return;
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ---- 对话（流式）----
  const handleSend = () => {
    const q = chatInput.trim();
    if (!q || streaming) return;
    const history = [...chatMessages, { role: 'user', content: q } as ChatMessage];
    setChatMessages([...history, { role: 'assistant', content: '' }]); // 预占位
    setChatInput('');
    setStreaming(true);

    const ac = new AbortController();
    abortRef.current = ac;
    streamChat(
      { analysis_ids: ids, task, messages: history },
      {
        onToken: (token) => {
          setChatMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === 'assistant') {
              next[next.length - 1] = { ...last, content: last.content + token };
            }
            return next;
          });
        },
        onDone: () => setStreaming(false),
        onError: (e) => {
          setStreaming(false);
          message.error(`对话失败: ${e.message}`);
        },
      },
      ac.signal,
    );
  };
  const handleStop = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  // 单个分析项渲染：markdown + 复制按钮
  const renderSection = (text: string, label: string) => (
    <div>
      <div style={{ textAlign: 'right', marginBottom: 4 }}>
        <Button size="small" type="text" icon={<CopyOutlined />}
          onClick={() => copyText(text, label)}>复制</Button>
      </div>
      <ReactMarkdown>{text || '（空）'}</ReactMarkdown>
    </div>
  );

  return (
    <div>
      {/* 顶部操作栏 */}
      <Space wrap style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/analysis')}>返回</Button>
        <span style={{ color: '#666' }}>维度：</span>
        <Select
          value={task}
          onChange={handleTaskChange}
          style={{ width: 140 }}
          options={[
            { label: '内容总结', value: 'summary' },
            { label: '视频知识总结', value: 'knowledge' },
          ]}
        />
        <Button type="primary" icon={<ThunderboltOutlined />}
          loading={digestMut.isPending} onClick={handleDigest}>
          生成大总结
        </Button>
        {digestText && (
          <>
            <Button icon={<CopyOutlined />} onClick={() => copyText(digestText, '大总结')}>复制大总结</Button>
            <Button icon={<DownloadOutlined />}
              onClick={() => downloadText(digestText, `digest_${task}_${Date.now()}.md`)}>
              下载
            </Button>
          </>
        )}
        <Tag color="blue">对比 {items.length} 条</Tag>
      </Space>

      {/* 大总结展示（生成后才出现，避免抢空间） */}
      {digestMut.isPending && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <div style={{ textAlign: 'center', padding: 16 }}>
            <Spin tip="正在生成大总结，请稍候（通常 10-30 秒）…" />
          </div>
        </Card>
      )}
      {digestText && (
        <Card size="small" title="跨视频大总结" style={{ marginBottom: 16 }}>
          <Typography>
            <ReactMarkdown>{digestText}</ReactMarkdown>
          </Typography>
        </Card>
      )}

      {/* 主体：16:8 分栏 */}
      <Row gutter={[16, 16]}>
        {/* 左栏：视频卡片栅格 */}
        <Col xs={24} lg={16}>
          {isLoading ? (
            <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
          ) : items.length === 0 ? (
            <Empty description="没有可对比的分析记录" />
          ) : (
            <Row gutter={[12, 12]}>
              {items.map((a) => {
                const tabs = [
                  ...(a.enabled_tasks?.includes('summary') ? [{
                    key: 'summary', label: '内容总结',
                    children: renderSection(a.content_summary, '内容总结'),
                  }] : []),
                  ...(a.enabled_tasks?.includes('script') ? [{
                    key: 'script', label: '脚本与制作手法',
                    children: renderSection(a.script_analysis, '脚本'),
                  }] : []),
                  ...(a.enabled_tasks?.includes('knowledge') ? [{
                    key: 'knowledge', label: '视频知识总结',
                    children: renderSection(a.knowledge_summary, '知识总结'),
                  }] : []),
                ];
                return (
                  <Col key={a.id} xs={24} md={12} xl={8}>
                    <Card
                      size="small"
                      title={
                        <Space size="small">
                          <span style={{ fontWeight: 500 }}>{a.title || '(无标题)'}</span>
                          <PlatformTag platform={a.platform} />
                        </Space>
                      }
                      extra={
                        <Button size="small" type="link" onClick={() => navigate(`/analysis/${a.id}`)}>
                          详情
                        </Button>
                      }
                    >
                      {tabs.length > 0 ? (
                        <Tabs size="small" items={tabs} defaultActiveKey={task} />
                      ) : (
                        <span style={{ color: '#999' }}>（无已生成的分析项）</span>
                      )}
                    </Card>
                  </Col>
                );
              })}
            </Row>
          )}
        </Col>

        {/* 右栏：对话面板（sticky 固定） */}
        <Col xs={24} lg={8}>
          <Card
            size="small"
            title={<Space size="small"><MessageOutlined /> 对话（基于{task === 'summary' ? '内容总结' : '知识总结'}）</Space>}
            extra={
              chatMessages.length > 0 && (
                <Button size="small" type="link" icon={<DeleteOutlined />}
                  onClick={() => setChatMessages([])}>清空</Button>
              )
            }
            styles={{ body: { padding: 0 } }}
            style={{ position: 'sticky', top: 16, height: 'calc(100vh - 140px)', display: 'flex', flexDirection: 'column' }}
            bodyStyle={{ display: 'flex', flexDirection: 'column', height: '100%' }}
          >
            {/* 消息列表 */}
            <div ref={msgListRef} style={{ flex: 1, overflow: 'auto', padding: 12 }}>
              {chatMessages.length === 0 && !streaming ? (
                <div style={{ color: '#999', fontSize: 12, padding: 8 }}>
                  对当前对比视频的总结内容提问，支持多轮追问。回答会逐字流式输出。上下文不保存，刷新即丢。
                </div>
              ) : (
                chatMessages.map((m, i) => (
                  <div key={i} style={{
                    margin: '8px 0', display: 'flex',
                    justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                  }}>
                    <div style={{
                      maxWidth: '88%', padding: '8px 12px', borderRadius: 8,
                      background: m.role === 'user' ? '#e6f4ff' : '#f5f5f5',
                    }}>
                      {m.content ? (
                        <Typography>
                          <ReactMarkdown>{m.content}</ReactMarkdown>
                        </Typography>
                      ) : (
                        <Spin size="small" />
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
            {/* 输入框 */}
            <div style={{ borderTop: '1px solid #f0f0f0', padding: 8 }}>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="提问，回车发送"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onPressEnter={handleSend}
                  disabled={streaming}
                />
                {streaming ? (
                  <Button danger icon={<DeleteOutlined />} onClick={handleStop}>停止</Button>
                ) : (
                  <Button type="primary" icon={<SendOutlined />} onClick={handleSend}>
                    发送
                  </Button>
                )}
              </Space.Compact>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
