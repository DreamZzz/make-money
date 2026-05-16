type Props = {
  title?: string;
  message: string;
};

export function LoadingPanel({ message = "加载中" }: Props) {
  return <div className="state-panel state-panel--loading">{message}</div>;
}

export function ErrorPanel({ title = "数据暂不可用", message }: Props) {
  return (
    <div className="state-panel state-panel--error">
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  );
}
