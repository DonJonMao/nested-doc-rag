# 数据中心工勘智能填表平台完整设计文档

文档日期：2026-06-19  
适用分支：`nested-doc-rag / agentic-rag`  
前端风格参考：VoltAgent `awesome-design-md` 中的 Apple 风格 DESIGN.md 体系  
目标形态：管理员维护知识分库与知识文档；普通用户选择分库、上传工勘单、启动自动填写任务、实时查看进度、下载回填后的 Excel。

---

## 1. 系统定位

本系统是一个面向数据中心工勘场景的 AI 填表平台。用户上传工勘 Excel 表后，系统根据所选知识分库调用后端填表流程，完成字段检索、LLM 仲裁、证据记录、Excel 回写和结果归档。管理员负责维护知识分库、上传知识文档、触发文档入库、查看索引状态。

系统不是普通聊天 RAG，不以“问答窗口”为中心；也不是传统后台管理系统，不以复杂表格和多级菜单为中心。系统主路径应极短：

```text
普通用户：登录 -> 选择分库 -> 上传工勘单 -> 启动填写 -> 看进度 -> 下载结果
管理员：登录 -> 知识库管理 -> 选择分库 -> 上传/删除文档 -> 自动入库 -> 确认 ready
```

现有 Go 后端已经把 Python Core 作为智能执行引擎：Python 负责知识库入库、Step15AgentRunner 填表、Excel safe writeback 和 artifact 输出；Go 负责 API、鉴权、文件、任务、Worker 编排、SSE、审核、下载与可观测性。本文档在这个边界上设计完整前端、后端优化点、数据库补充、部署与验收标准。

---

## 2. 总体架构

### 2.1 系统组件

```text
Browser / Vue Frontend
        |
        | HTTPS / REST / SSE
        v
Go API Server
        |
        | SQL / Queue / Object Storage
        v
PostgreSQL + Redis + MinIO/S3
        |
        | Redis Queue
        v
Go Worker
        |
        | CLI Invocation
        v
Python Core
        |
        | Vector Search / Model Calls
        v
Qdrant + Embedding Service + Rerank Service + Chat LLM
```

### 2.2 职责边界

| 层 | 职责 | 明确不做 |
|---|---|---|
| Vue 前端 | 登录、角色路由、分库选择、文件上传、任务列表、SSE 进度、下载结果、Apple 风格 UI | 不直接访问 Python、不直接访问 Qdrant、不在浏览器执行填表逻辑 |
| Go API Server | 鉴权、RBAC、文件上传、知识库管理、填表任务创建、状态查询、SSE、artifact 下载、审计 | 不重写 RAG、不直接调用 LLM 生成答案 |
| Go Worker | 消费任务、拉起 Python CLI、同步状态、归档 artifact、发送 run events、控制并发和取消 | 不解析 Office、不做向量检索 |
| Python Core | 文档解析、切分、embedding、Qdrant 入库、Step15AgentRunner、Excel 回写、artifact 生成 | 不管理用户、权限、前端会话 |
| PostgreSQL | 用户、角色、workspace、知识库、文档元数据、任务、事件、artifact 元数据、审计 | 不存大体积 Excel 二进制 |
| Redis | 任务队列、SSE pub/sub、worker heartbeat、分布式锁、短期状态 | 不做最终业务状态源 |
| MinIO/S3 | 原始知识文档、上传工勘单、Python 输出 artifact、回填 Excel | 不做权限判定 |

### 2.3 完整业务闭环

```text
管理员维护知识库：
1. 管理员登录
2. 查看固定分库
3. 进入某个分库
4. 上传知识文档
5. 后端保存文件到对象存储
6. 后端保存 knowledge_documents 元数据
7. 后端创建 ingestion job
8. Worker 调 Python Core 做文档解析和 Qdrant 入库
9. Python 生成 ingestion artifact / manifest
10. Worker 同步 index version 状态
11. 前端显示分库 ready

用户自动填表：
1. 用户登录
2. 前端获取可用知识分库 options
3. 用户选择分库
4. 用户上传工勘 Excel
5. 后端保存 form file 到对象存储
6. 用户点击开始填写
7. 后端创建 fill_run + fill_form job
8. Worker 调 Python Step15AgentRunner
9. Python 输出 filled_form.xlsx、run_summary、trace、review_items 等 artifact
10. Worker 归档 artifact 并同步 fill_run 状态
11. 前端通过 SSE 实时显示进度
12. 任务完成后前端展示下载按钮
```

---

## 3. 用户、角色与权限

### 3.1 角色定义

系统采用全局角色 + workspace 成员角色。产品上先暴露两类用户：

| 产品角色 | 后端角色 | 权限 |
|---|---|---|
| 管理员 | global `admin` | 登录、填表、查看自己的任务、查看全部任务、知识库管理、用户/工作区初始化 |
| 普通用户 | global `operator` 或 `viewer` + workspace member | 登录、选择 ready 分库、上传工勘单、创建自己的填表任务、查看自己的任务、下载自己的结果 |

前端可以只用 `user.roles.includes('admin')` 判断是否显示知识库管理入口；后端必须做真实权限拦截。

### 3.2 权限矩阵

| 功能 | 管理员 | 普通用户 |
|---|---:|---:|
| 登录 | ✓ | ✓ |
| 查看当前用户信息 | ✓ | ✓ |
| 查看分库 options | ✓ | ✓ |
| 进入知识库管理页 | ✓ | ✗ |
| 查看分库文档列表 | ✓ | ✗ |
| 上传知识文档 | ✓ | ✗ |
| 删除知识文档 | ✓ | ✗ |
| 创建知识库入库任务 | ✓ | ✗ |
| 设置当前 index version | ✓ | ✗ |
| 上传工勘单 | ✓ | ✓ |
| 创建填表任务 | ✓ | ✓ |
| 查看自己的填表任务 | ✓ | ✓ |
| 查看所有填表任务 | ✓ | ✗ |
| 下载自己的 filled form | ✓ | ✓ |
| 下载他人的 filled form | ✓ | ✗ |
| 取消自己的任务 | ✓ | ✓ |
| 取消他人任务 | ✓ | ✗ |

### 3.3 后端权限要求

知识库管理写接口必须强制 `admin`：

```text
POST   /api/v1/knowledge-bases
PATCH  /api/v1/knowledge-bases/{kb_id}
DELETE /api/v1/knowledge-bases/{kb_id}
POST   /api/v1/knowledge-bases/{kb_id}/documents
DELETE /api/v1/documents/{doc_id}
POST   /api/v1/knowledge-bases/{kb_id}/ingestion-runs
POST   /api/v1/knowledge-bases/{kb_id}/current-index-version
```

普通用户填表时只访问轻量分库选择接口：

```text
GET /api/v1/knowledge-bases/options?workspace_id={workspace_id}
```

填表任务必须按当前用户过滤：

```text
普通用户：只能看到 created_by = current_user_id 的 fill_runs
管理员：可以看到 workspace 下全部 fill_runs，也可以 mine=true 只看自己的
```

---

## 4. 知识分库模型

### 4.1 固定分库

系统初始化时创建以下固定分库：

| 展示名 | namespace | 说明 |
|---|---|---|
| 西咸1号楼 | `xixian_1` | 西咸园区 1 号楼 |
| 西咸2号楼 | `xixian_2` | 西咸园区 2 号楼 |
| 西咸3号楼 | `xixian_3` | 西咸园区 3 号楼 |
| 西咸4号楼 | `xixian_4` | 西咸园区 4 号楼 |
| 西咸5号楼 | `xixian_5` | 西咸园区 5 号楼 |
| 西咸6号楼 | `xixian_6` | 西咸园区 6 号楼 |
| 城东浐灞 | `chengdong_chanba` | 城东浐灞区域 |
| 西安 | `xian` | 西安区域 |
| 咸阳 | `xianyang` | 咸阳区域 |

### 4.2 建模原则

```text
一个分库 = 一个 knowledge_base
knowledge_base.namespace = Python/Qdrant 使用的 target namespace
knowledge_base.current_index_version_id = 当前可用于填表的索引版本
knowledge_documents.namespace 默认继承 knowledge_base.namespace
fill_run.knowledge_base_id 指向所选分库
fill_run.target_namespace 由 knowledge_base.namespace 自动推导
```

不建议让前端手写 namespace，也不建议把多个分库塞进一个 knowledge_base 再靠 UI 字段区分。namespace 是 RAG 检索的物理隔离边界，应在数据库层成为稳定字段。

### 4.3 分库状态

分库状态由 `knowledge_bases`、`knowledge_index_versions`、`knowledge_documents` 聚合得到：

| 状态 | 含义 | 前端表现 |
|---|---|---|
| `empty` | 没有文档或没有成功索引 | 灰色，不允许普通用户选择 |
| `building` | 正在入库 | 蓝色进度，不允许启动填表 |
| `ready` | 有当前 ready index version | 绿色/深色，允许选择 |
| `stale` | 文档有变更但索引未更新 | 黄色提示，管理员应重新入库，普通用户默认不可选 |
| `failed` | 最近一次入库失败 | 红色提示，不允许选择 |

---

## 5. 前端技术栈

### 5.1 推荐栈

```text
Vue 3
Vite
TypeScript
Vue Router
Pinia
Axios
Element Plus
@microsoft/fetch-event-source
dayjs
lucide-vue-next 或 @element-plus/icons-vue
```

说明：SSE 接口需要携带 Bearer token。浏览器原生 `EventSource` 不能方便地添加 `Authorization` header，因此推荐 `@microsoft/fetch-event-source`。

### 5.2 工程目录

```text
web/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.ts
    App.vue
    styles/
      tokens.css
      element-overrides.css
      layout.css
      motion.css
    api/
      http.ts
      auth.api.ts
      workspace.api.ts
      knowledge.api.ts
      files.api.ts
      forms.api.ts
      fillRuns.api.ts
      events.api.ts
      artifacts.api.ts
    stores/
      auth.store.ts
      workspace.store.ts
      knowledge.store.ts
      fillRun.store.ts
      ui.store.ts
    router/
      index.ts
      guards.ts
    layouts/
      AuthLayout.vue
      AppLayout.vue
    components/
      nav/
        GlobalNav.vue
        SubNav.vue
      common/
        UtilityCard.vue
        PillButton.vue
        StatusPill.vue
        EmptyState.vue
        ConfirmDialog.vue
        ErrorInline.vue
      upload/
        AppleUploadDropzone.vue
      knowledge/
        KnowledgeBaseRail.vue
        KnowledgeBaseHeader.vue
        KnowledgeDocumentTable.vue
        IngestionProgressPanel.vue
      fill/
        KnowledgeBaseSelector.vue
        FillRunCard.vue
        RunProgressBar.vue
        RunEventTimeline.vue
        ArtifactDownloadPanel.vue
    views/
      LoginView.vue
      FillCreateView.vue
      FillHistoryView.vue
      FillRunDetailView.vue
      AdminKnowledgeView.vue
      NotFoundView.vue
```

### 5.3 路由

```ts
export const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
  },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        redirect: '/fill',
      },
      {
        path: '/fill',
        name: 'fill-create',
        component: () => import('@/views/FillCreateView.vue'),
      },
      {
        path: '/fill/history',
        name: 'fill-history',
        component: () => import('@/views/FillHistoryView.vue'),
      },
      {
        path: '/fill/runs/:runId',
        name: 'fill-run-detail',
        component: () => import('@/views/FillRunDetailView.vue'),
      },
      {
        path: '/admin/knowledge',
        name: 'admin-knowledge',
        component: () => import('@/views/AdminKnowledgeView.vue'),
        meta: { requiresAdmin: true },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
  },
]
```

路由守卫：

```ts
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.meta.requiresAuth && !auth.accessToken) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (auth.accessToken && !auth.user) {
    await auth.fetchMe()
  }

  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return { name: 'fill-create' }
  }

  if (to.name === 'login' && auth.accessToken) {
    return auth.isAdmin ? { name: 'admin-knowledge' } : { name: 'fill-create' }
  }
})
```

---

## 6. Apple-inspired 视觉设计系统

### 6.1 设计原则

采用 Apple 风格不是复制 Apple 官网，而是抽象其界面语言：高留白、低噪声、克制层级、系统字体、单一蓝色交互、白/米白/近黑表面、pill 控件、毛玻璃导航、轻量卡片。

设计关键词：

```text
Premium whitespace
Soft hierarchy
System typography
One primary blue
Large rounded surfaces
Minimal shadow
Cinematic but restrained
Task-first layout
```

禁用：

```text
传统大侧边栏后台
高饱和渐变背景
多品牌色混用
大面积厚重阴影
密集表格堆砌
复杂装饰图标
低信息价值大屏化设计
```

### 6.2 设计 Token

```css
:root {
  --gk-blue: #0066cc;
  --gk-blue-hover: #0071e3;
  --gk-blue-on-dark: #2997ff;

  --gk-ink: #1d1d1f;
  --gk-ink-2: #333333;
  --gk-ink-3: #6e6e73;
  --gk-ink-4: #86868b;

  --gk-white: #ffffff;
  --gk-page: #f5f5f7;
  --gk-page-soft: #fafafc;
  --gk-black: #000000;
  --gk-surface-dark: #1d1d1f;
  --gk-surface-dark-2: #272729;
  --gk-hairline: #d2d2d7;
  --gk-hairline-soft: #e8e8ed;

  --gk-success: #1d7f43;
  --gk-warning: #b36b00;
  --gk-danger: #c9342b;
  --gk-info: #0066cc;

  --gk-radius-xs: 6px;
  --gk-radius-sm: 8px;
  --gk-radius-md: 11px;
  --gk-radius-lg: 18px;
  --gk-radius-xl: 28px;
  --gk-radius-pill: 9999px;

  --gk-nav-height: 44px;
  --gk-subnav-height: 52px;
  --gk-content-max: 1440px;

  --gk-space-1: 4px;
  --gk-space-2: 8px;
  --gk-space-3: 12px;
  --gk-space-4: 16px;
  --gk-space-5: 20px;
  --gk-space-6: 24px;
  --gk-space-8: 32px;
  --gk-space-10: 40px;
  --gk-space-12: 48px;
  --gk-space-16: 64px;

  --gk-ease: cubic-bezier(0.28, 0.11, 0.32, 1);
}
```

### 6.3 字体层级

```css
html {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", sans-serif;
  color: var(--gk-ink);
  background: var(--gk-page);
}

.gk-display {
  font-size: clamp(42px, 5vw, 72px);
  line-height: 1.05;
  font-weight: 600;
  letter-spacing: -0.035em;
}

.gk-page-title {
  font-size: clamp(32px, 3vw, 48px);
  line-height: 1.08;
  font-weight: 600;
  letter-spacing: -0.025em;
}

.gk-section-title {
  font-size: 28px;
  line-height: 1.18;
  font-weight: 600;
  letter-spacing: -0.018em;
}

.gk-card-title {
  font-size: 21px;
  line-height: 1.28;
  font-weight: 600;
  letter-spacing: -0.012em;
}

.gk-body {
  font-size: 17px;
  line-height: 1.47;
  font-weight: 400;
}

.gk-caption {
  font-size: 13px;
  line-height: 1.38;
  font-weight: 400;
  color: var(--gk-ink-3);
}
```

### 6.4 组件风格

#### GlobalNav

```text
高度：44px
背景：#000
文字：半透明白色
当前项：白色
hover：白色
布局：居中最大宽 1440px
```

#### SubNav

```text
高度：52px
背景：rgba(245,245,247,0.72)
效果：backdrop-filter: saturate(180%) blur(20px)
下边线：1px rgba(0,0,0,0.08)
```

#### Card

```text
背景：白色
圆角：18px
边框：1px solid #e8e8ed
阴影：默认无阴影
hover：轻微边框加深，不做大投影
```

#### Button

```text
主按钮：蓝底白字 pill
次按钮：白底蓝字 pill，1px hairline
危险按钮：白底红字，不使用大面积红色
active：scale(0.96)
```

#### Upload Dropzone

```text
大圆角 28px
米白背景
虚线边框
拖拽 hover 时边框变蓝
主文案 21px，副文案 14px
```

### 6.5 Element Plus 覆写

```css
:root {
  --el-color-primary: var(--gk-blue);
  --el-color-primary-light-3: #3385d6;
  --el-color-primary-light-5: #66a3e0;
  --el-color-primary-light-7: #99c2eb;
  --el-color-primary-light-9: #eaf4ff;
  --el-border-radius-base: var(--gk-radius-sm);
  --el-font-size-base: 17px;
  --el-text-color-primary: var(--gk-ink);
  --el-bg-color-page: var(--gk-page);
}

.el-button {
  border-radius: var(--gk-radius-pill);
  font-weight: 400;
  box-shadow: none;
  transition: transform 160ms var(--gk-ease), background-color 160ms var(--gk-ease);
}

.el-button--primary {
  padding: 10px 22px;
  border-color: var(--gk-blue);
  background: var(--gk-blue);
}

.el-button--primary:hover {
  background: var(--gk-blue-hover);
  border-color: var(--gk-blue-hover);
}

.el-button:active {
  transform: scale(0.96);
}

.el-card {
  border-radius: var(--gk-radius-lg);
  border: 1px solid var(--gk-hairline-soft);
  box-shadow: none;
}

.el-input__wrapper,
.el-select__wrapper,
.el-textarea__inner {
  border-radius: var(--gk-radius-md);
  box-shadow: 0 0 0 1px var(--gk-hairline-soft) inset;
}

.el-input__wrapper.is-focus,
.el-select__wrapper.is-focused {
  box-shadow: 0 0 0 2px var(--gk-blue) inset;
}

.el-table {
  --el-table-border-color: var(--gk-hairline-soft);
  --el-table-header-bg-color: #fbfbfd;
  border-radius: var(--gk-radius-lg);
  overflow: hidden;
}
```

---

## 7. 前端页面设计

### 7.1 登录页 `/login`

#### 目标

用户登录系统，并根据角色进入不同默认页面。

#### 布局

```text
黑色 GlobalNav
米白全屏背景
中央登录卡片：420px 宽，白底，18px 圆角
标题：工勘智能填表
副标题：选择知识分库，上传工勘单，自动生成回填结果
表单：账号、密码
主按钮：登录
底部：版本号 / 后端连接状态
```

#### 交互

```text
1. 用户输入账号密码
2. POST /api/v1/auth/login
3. 保存 access_token / refresh_token
4. GET /api/v1/auth/me
5. admin 跳转 /admin/knowledge
6. 普通用户跳转 /fill
```

#### 错误处理

| 错误 | 前端表现 |
|---|---|
| 401 | 账号或密码错误 |
| 5xx | 服务暂不可用，请稍后重试 |
| 网络失败 | 无法连接后端服务 |
| token 保存失败 | 登录状态保存失败，请刷新重试 |

### 7.2 工勘单自动填写页 `/fill`

#### 目标

让用户以最低认知成本创建填表任务。

#### 页面结构

```text
Hero：
  标题：上传工勘单，自动完成字段填写
  副标题：选择知识分库后，系统会检索对应资料并生成可下载的回填 Excel。

主操作区：
  左卡片：选择知识分库
  右卡片：上传工勘单

参数区：
  机房/房间上下文 room_context
  可选：填写行范围 rows，默认隐藏在高级设置

底部：
  最近任务 3-5 条
```

#### 分库选择

以 pill chip 或大卡片显示：

```text
西咸1号楼  ready
西咸2号楼  ready
西咸3号楼  building
西咸4号楼  ready
西咸5号楼  empty
西咸6号楼  ready
城东浐灞    ready
西安        ready
咸阳        ready
```

只允许选择 `ready` 分库。`building/stale/failed/empty` 可以显示，但按钮 disabled，并给出原因。

#### 上传工勘单

支持：

```text
.xlsx
.xlsm，可按后端安全策略决定是否允许
最大大小按后端配置
拖拽上传
点击选择
上传进度
上传成功显示文件名、大小、SHA256 前 8 位
```

#### 创建任务

推荐前端调用产品化接口：

```http
POST /api/v1/fill-runs/simple
```

请求体：

```json
{
  "workspace_id": "ws_uuid",
  "knowledge_base_id": "kb_uuid",
  "form_file_id": "form_file_uuid",
  "room_context": "西咸4号楼 301机房"
}
```

后端自动补齐：

```text
target_namespace = knowledge_bases.namespace
index_version_id = knowledge_bases.current_index_version_id
global_namespace = global
rows = 默认配置，例如 4-144
retrieval_mode = layered
prompt_version = step15_compat
judge = false
use_judge_cache = false
writeback = true
```

创建成功后跳转：

```text
/fill/runs/{run_id}
```

### 7.3 我的填写任务页 `/fill/history`

#### 目标

用户退出系统后重新进入，仍能看到自己发起的填表任务和下载历史结果。

#### 布局

```text
顶部：标题“我的填写任务” + 状态筛选
主体：任务卡片列表
右侧/顶部：搜索框，按文件名、分库、时间筛选
```

#### 任务卡片字段

```text
任务状态
工勘单文件名
所属分库
创建时间
更新时间
进度百分比
创建人，管理员视图可见
操作：查看详情 / 下载结果 / 取消
```

#### 请求

```http
GET /api/v1/fill-runs?workspace_id={workspace_id}&mine=true&status={status}&limit=20&offset=0
```

普通用户即使不传 `mine=true`，后端也必须强制只返回自己的任务。

#### 状态分组

```text
进行中：created / queued / running / cancel_requested
完成：succeeded / completed_with_failures
失败：failed
取消：canceled
```

### 7.4 任务详情页 `/fill/runs/:runId`

#### 目标

展示当前填表进度、运行日志、结果下载、失败原因。

#### 页面结构

```text
顶部任务摘要：
  文件名
  分库
  创建时间
  状态
  进度

中部：
  左侧：进度条 + 当前步骤
  右侧：下载面板

下部：
  事件时间线
  审核/风险提示
  错误详情
```

#### 初始加载

```text
1. GET /api/v1/fill-runs/{run_id}
2. GET /api/v1/fill-runs/{run_id}/result，若任务已完成
3. 建立 SSE：GET /api/v1/runs/{run_id}/events?workspace_id=...&after_sequence=...
```

#### SSE 事件处理

前端维护：

```ts
interface RunEvent {
  id: string
  run_id: string
  sequence: number
  event_type: string
  message: string
  progress_done?: number
  progress_total?: number
  payload?: Record<string, unknown>
  created_at: string
}
```

进度计算：

```ts
const percent = progress_total > 0
  ? Math.round((progress_done / progress_total) * 100)
  : status === 'succeeded'
    ? 100
    : 0
```

断线重连：

```text
保存 last_sequence
SSE 断线后用 after_sequence=last_sequence 重连
先 replay persisted events，再接 live events
```

#### 下载区域

主按钮：

```text
下载填好的工勘单
GET /api/v1/fill-runs/{run_id}/download/filled-form
```

次级按钮：

```text
运行摘要 run-summary
审核项 review-items
Trace trace
全部 artifact 列表
```

### 7.5 知识库管理页 `/admin/knowledge`

#### 目标

管理员查看固定分库、管理分库文档、触发入库、确认索引可用。

#### 布局

```text
页面宽度：最大 1440px
左侧分库 Rail：280px
右侧详情区：自适应

左侧：
  分库列表
  每项显示：名称、状态、文档数、最近入库时间

右侧：
  分库 Header
  索引状态卡片
  文档表格
  上传文档 Dropzone
  入库事件面板
```

#### 分库 Header

```text
名称：西咸4号楼
namespace：xixian_4
当前索引版本：idxv_xxx
索引状态：ready
Qdrant collection：datacenter_chunks_v1
最近入库时间：2026-06-19 14:31
```

#### 文档表格

字段：

```text
文件名
文件类型
document_role
namespace
状态
大小
上传人
上传时间
最近入库时间
操作：下载 / 删除
```

操作规则：

```text
下载：GET /api/v1/files/{file_id}/download
删除：DELETE /api/v1/documents/{doc_id}
上传：POST /api/v1/knowledge-bases/{kb_id}/documents?auto_ingest=true
```

#### 上传知识文档

上传请求：

```http
POST /api/v1/knowledge-bases/{kb_id}/documents?auto_ingest=true
Content-Type: multipart/form-data
```

表单字段：

```text
document_role = knowledge_base
namespace = 可省略，后端默认 knowledge_base.namespace
file = 文档文件
```

后端返回：

```json
{
  "document": {},
  "ingestion_job": {},
  "index_version": {}
}
```

#### 删除知识文档后的索引一致性

删除知识文档不能只删数据库元数据，否则 Qdrant 中旧向量仍可能被检索到。完整处理流程：

```text
1. API 软删除 knowledge_documents 记录，状态变为 deleted
2. API 将 knowledge_base.status 标记为 stale
3. API 创建索引刷新任务，mode = rebuild_namespace 或 delete_document_vectors
4. Worker 调 Python Core 清理该 document_id 对应向量，或重建 namespace
5. 成功后创建新的 ready index version
6. current_index_version_id 指向新版本
```

如果 Python 暂时不支持按 document_id 删除向量，则使用最稳妥的 `rebuild_namespace`：重新读取当前分库未删除文档，重建该 namespace 的索引。

---

## 8. 前端 API 封装

### 8.1 Axios 实例

```ts
import axios from 'axios'
import { useAuthStore } from '@/stores/auth.store'

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.accessToken) {
    config.headers.Authorization = `Bearer ${auth.accessToken}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const auth = useAuthStore()
    const status = error.response?.status
    const original = error.config

    if (status === 401 && !original.__retried && auth.refreshToken) {
      original.__retried = true
      await auth.refresh()
      original.headers.Authorization = `Bearer ${auth.accessToken}`
      return http(original)
    }

    return Promise.reject(error)
  },
)
```

### 8.2 Auth API

```ts
export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: 'Bearer'
  expires_in: number
  user: User
}

export function login(payload: LoginRequest) {
  return http.post<LoginResponse>('/api/v1/auth/login', payload)
}

export function getMe() {
  return http.get<MeResponse>('/api/v1/auth/me')
}
```

### 8.3 Knowledge API

```ts
export interface KnowledgeBaseOption {
  id: string
  workspace_id: string
  name: string
  namespace: string
  qdrant_collection: string
  current_index_version_id?: string
  status: 'empty' | 'building' | 'ready' | 'stale' | 'failed'
  document_count: number
  updated_at: string
}

export function listKnowledgeOptions(workspaceId: string) {
  return http.get<KnowledgeBaseOption[]>('/api/v1/knowledge-bases/options', {
    params: { workspace_id: workspaceId },
  })
}

export function listKnowledgeDocuments(kbId: string) {
  return http.get<KnowledgeDocument[]>(`/api/v1/knowledge-bases/${kbId}/documents`)
}

export function uploadKnowledgeDocument(kbId: string, file: File) {
  const form = new FormData()
  form.append('document_role', 'knowledge_base')
  form.append('file', file)
  return http.post(`/api/v1/knowledge-bases/${kbId}/documents`, form, {
    params: { auto_ingest: true },
  })
}
```

### 8.4 Forms API

```ts
export function uploadForm(workspaceId: string, file: File) {
  const form = new FormData()
  form.append('workspace_id', workspaceId)
  form.append('file', file)
  return http.post<FormFile>('/api/v1/forms', form)
}
```

### 8.5 Fill Runs API

```ts
export interface CreateSimpleFillRunRequest {
  workspace_id: string
  knowledge_base_id: string
  form_file_id: string
  room_context?: string
}

export function createSimpleFillRun(payload: CreateSimpleFillRunRequest) {
  return http.post<FillRun>('/api/v1/fill-runs/simple', payload)
}

export function listMyFillRuns(workspaceId: string, status?: string) {
  return http.get<FillRun[]>('/api/v1/fill-runs', {
    params: { workspace_id: workspaceId, mine: true, status },
  })
}

export function getFillRun(runId: string) {
  return http.get<FillRun>(`/api/v1/fill-runs/${runId}`)
}

export function getFillRunResult(runId: string) {
  return http.get<FillRunResult>(`/api/v1/fill-runs/${runId}/result`)
}

export function downloadFilledForm(runId: string) {
  window.open(`${API_BASE}/api/v1/fill-runs/${runId}/download/filled-form`, '_blank')
}
```

下载接口如果需要 Authorization header，不能简单 `window.open`。更稳妥实现：

```ts
export async function downloadWithAuth(url: string, filename: string) {
  const auth = useAuthStore()
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { Authorization: `Bearer ${auth.accessToken}` },
  })
  if (!res.ok) throw new Error('download failed')
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  a.click()
  URL.revokeObjectURL(objectUrl)
}
```

### 8.6 SSE API

```ts
import { fetchEventSource } from '@microsoft/fetch-event-source'

export function subscribeRunEvents(args: {
  runId: string
  workspaceId: string
  afterSequence?: number
  onEvent: (event: RunEvent) => void
  onError?: (error: unknown) => void
  signal?: AbortSignal
}) {
  const auth = useAuthStore()
  const url = new URL(`${API_BASE}/api/v1/runs/${args.runId}/events`)
  url.searchParams.set('workspace_id', args.workspaceId)
  if (args.afterSequence) {
    url.searchParams.set('after_sequence', String(args.afterSequence))
  }

  return fetchEventSource(url.toString(), {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${auth.accessToken}`,
    },
    signal: args.signal,
    onmessage(msg) {
      if (!msg.data) return
      args.onEvent(JSON.parse(msg.data))
    },
    onerror(err) {
      args.onError?.(err)
      throw err
    },
  })
}
```

---

## 9. 后端接口完整设计

### 9.1 保留现有接口

现有接口继续作为底层能力存在：

```text
Auth:
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me

Workspace:
GET  /api/v1/workspaces
POST /api/v1/workspaces
GET  /api/v1/workspaces/{workspace_id}

Files:
POST   /api/v1/files
GET    /api/v1/files
GET    /api/v1/files/{file_id}
DELETE /api/v1/files/{file_id}
GET    /api/v1/files/{file_id}/download

Forms:
POST /api/v1/forms
GET  /api/v1/forms
GET  /api/v1/forms/{form_id}

Fill Runs:
POST /api/v1/fill-runs
GET  /api/v1/fill-runs
GET  /api/v1/fill-runs/{run_id}
POST /api/v1/fill-runs/{run_id}/cancel
GET  /api/v1/fill-runs/{run_id}/artifacts
GET  /api/v1/fill-runs/{run_id}/download/{artifact_kind}
GET  /api/v1/fill-runs/{run_id}/result

Events:
GET /api/v1/runs/{run_id}/events

Knowledge:
POST   /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases/{kb_id}
GET    /api/v1/knowledge-bases/{kb_id}/documents
POST   /api/v1/knowledge-bases/{kb_id}/documents
DELETE /api/v1/documents/{doc_id}
POST   /api/v1/knowledge-bases/{kb_id}/ingestion-runs
GET    /api/v1/knowledge-bases/{kb_id}/ingestion-runs
GET    /api/v1/ingestion-runs/{ingestion_job_id}
POST   /api/v1/ingestion-runs/{ingestion_job_id}/cancel
```

### 9.2 新增产品化接口

#### 9.2.1 分库 options

```http
GET /api/v1/knowledge-bases/options?workspace_id={workspace_id}
Authorization: Bearer <token>
```

响应：

```json
{
  "code": "OK",
  "data": [
    {
      "id": "kb_uuid",
      "workspace_id": "ws_uuid",
      "name": "西咸4号楼",
      "namespace": "xixian_4",
      "qdrant_collection": "datacenter_chunks_v1",
      "current_index_version_id": "idxv_uuid",
      "status": "ready",
      "document_count": 12,
      "last_ingested_at": "2026-06-19T06:30:00Z"
    }
  ]
}
```

权限：普通用户可读，但只返回可用于填表的必要字段，不返回文档明细。

#### 9.2.2 简化创建填表任务

```http
POST /api/v1/fill-runs/simple
Authorization: Bearer <token>
Content-Type: application/json
```

请求：

```json
{
  "workspace_id": "ws_uuid",
  "knowledge_base_id": "kb_uuid",
  "form_file_id": "file_uuid",
  "room_context": "西咸4号楼 301机房"
}
```

服务端校验：

```text
1. 用户有 workspace 读写权限
2. form_file_id 属于当前 workspace
3. knowledge_base_id 属于当前 workspace
4. knowledge_base.status = ready
5. current_index_version_id 非空
6. namespace 非空
7. form file 类型合法
```

服务端派生：

```text
target_namespace = knowledge_base.namespace
index_version_id = knowledge_base.current_index_version_id
qdrant_collection = knowledge_base.qdrant_collection
rows = config.python.step15_default_rows
global_namespace = config.python.global_namespace 或 "global"
retrieval_mode = config.python.step15_default_retrieval_mode
prompt_version = config.python.step15_default_prompt_version
writeback = true
```

响应：

```json
{
  "code": "OK",
  "data": {
    "id": "run_uuid",
    "workspace_id": "ws_uuid",
    "form_file_id": "file_uuid",
    "knowledge_base_id": "kb_uuid",
    "target_namespace": "xixian_4",
    "status": "queued",
    "progress_done": 0,
    "progress_total": 0,
    "created_by": "user_uuid",
    "created_at": "2026-06-19T06:31:00Z"
  }
}
```

#### 9.2.3 我的任务

在现有列表接口增加 `mine` 参数：

```http
GET /api/v1/fill-runs?workspace_id={workspace_id}&mine=true&status=running&limit=20&offset=0
```

后端规则：

```text
普通用户：强制 mine=true
管理员：mine=true 时看自己的；mine=false 或省略时看 workspace 全部
```

#### 9.2.4 上传知识文档自动入库

```http
POST /api/v1/knowledge-bases/{kb_id}/documents?auto_ingest=true
Authorization: Bearer <admin_token>
Content-Type: multipart/form-data
```

表单：

```text
document_role = knowledge_base
namespace = 可省略
file = 文件
```

后端行为：

```text
1. 保存文件
2. 创建 knowledge_document
3. 如果 auto_ingest=true，创建 ingestion run
4. 返回 document、ingestion_job、index_version
```

#### 9.2.5 删除知识文档并刷新索引

```http
DELETE /api/v1/documents/{doc_id}?reindex=true
Authorization: Bearer <admin_token>
```

后端行为：

```text
1. 软删除 document
2. 标记 knowledge_base.status = stale
3. reindex=true 时创建 ingestion run，mode=rebuild_namespace
4. 返回 document + optional ingestion_job
```

---

## 10. 后端优化点

### 10.1 knowledge_bases 增加 namespace

问题：当前上传知识文档和创建入库任务需要显式传 `namespace`，填表任务也需要 `target_namespace`。这会导致前端和用户界面持有 RAG 内部参数。

优化：

```text
knowledge_bases.namespace 作为分库稳定检索命名空间
所有文档、入库、填表默认从 knowledge_base.namespace 推导
```

### 10.2 普通用户任务隔离

问题：如果普通用户都在同一 workspace，仅按 workspace 过滤任务会看到他人的填表任务。

优化：

```text
ListFillRuns、GetFillRun、CancelFillRun、DownloadArtifact 全部加 owner 约束
普通用户：created_by = current_user_id
管理员：workspace-scoped all access
```

### 10.3 填表任务产品化封装

问题：现有 `CreateFillRunRequest` 暴露 `target_namespace`、`rows`、`retrieval_mode`、`prompt_version`、`judge` 等实验参数，不适合普通用户页面。

优化：新增 `/fill-runs/simple`。前端只传分库、工勘单、room_context。后端做参数派生。

### 10.4 知识文档自动入库

问题：上传文档后必须再手动创建 ingestion run，管理员容易误以为上传即生效。

优化：支持 `auto_ingest=true`。前端上传后直接进入入库事件面板。

### 10.5 删除文档后的索引一致性

问题：只软删除数据库文档，Qdrant 旧向量仍可能被检索。

优化：删除文档后标记分库 `stale`，并创建 `rebuild_namespace` 或 `delete_document_vectors` 入库任务。若 Python 暂无增量删除能力，则以重建 namespace 保证一致性。

### 10.6 全局 Python 并发控制

需求：“填写任务需要在后端固定一个进程”。系统应支持浏览器退出后任务继续执行，同时可以配置全局只跑一个填表 Python 进程。

配置：

```yaml
jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 1
```

行为：

```text
用户可以提交多个任务
后端按队列排队
同一时刻只有一个 Python 填表进程运行
浏览器退出不影响 Worker 执行
```

### 10.7 run_events 强化

SSE 断线恢复依赖稳定事件序号。

优化：

```text
run_events.sequence 在同一 run_id 下严格递增
SSE after_sequence 先查 DB replay，再接 Redis live stream
前端保存 last_sequence
```

### 10.8 artifact 下载权限

下载 filled form 不能只判断 artifact_id 存在，必须反查 run 权限。

规则：

```text
普通用户只能下载自己 run 的 artifacts
管理员可以下载 workspace 内 artifacts
artifact 必须属于该 run
artifact_kind 必须在允许列表中
```

### 10.9 审计日志

记录：

```text
登录成功/失败
上传知识文档
删除知识文档
创建 ingestion run
上传工勘单
创建 fill run
取消 fill run
下载 artifact
权限拒绝
```

---

## 11. 数据库设计

以下是完整产品形态下需要关注的核心表。现有表可复用，新增字段通过 migration 补充。

### 11.1 users

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 11.2 user_roles

```sql
CREATE TABLE user_roles (
  user_id UUID NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'reviewer', 'viewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role)
);
```

### 11.3 workspaces

```sql
CREATE TABLE workspaces (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 11.4 workspace_members

```sql
CREATE TABLE workspace_members (
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  user_id UUID NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner', 'operator', 'reviewer', 'viewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);
```

### 11.5 files

存储上传文件元数据。Excel、知识文档、proof attachment 都走对象存储，数据库只存元数据和 object key。

```sql
CREATE TABLE files (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  file_category TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  content_type TEXT,
  size_bytes BIGINT NOT NULL,
  sha256 TEXT NOT NULL,
  storage_bucket TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  uploaded_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_files_workspace_category_created
ON files(workspace_id, file_category, created_at DESC);
```

### 11.6 knowledge_bases

需要新增 `namespace` 和聚合状态字段。

```sql
ALTER TABLE knowledge_bases
ADD COLUMN IF NOT EXISTS namespace TEXT,
ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'empty',
ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS document_count INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_bases_workspace_namespace
ON knowledge_bases(workspace_id, namespace)
WHERE namespace IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_workspace_status
ON knowledge_bases(workspace_id, status, updated_at DESC);
```

推荐最终表结构：

```sql
CREATE TABLE knowledge_bases (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  name TEXT NOT NULL,
  namespace TEXT NOT NULL,
  description TEXT,
  qdrant_collection TEXT NOT NULL,
  current_index_version_id UUID,
  status TEXT NOT NULL DEFAULT 'empty'
    CHECK (status IN ('empty', 'building', 'ready', 'stale', 'failed', 'archived')),
  document_count INTEGER NOT NULL DEFAULT 0,
  last_ingested_at TIMESTAMPTZ,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id, namespace)
);
```

### 11.7 knowledge_documents

```sql
ALTER TABLE knowledge_documents
ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_kb_status_created_at
ON knowledge_documents(knowledge_base_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_file
ON knowledge_documents(file_id);
```

推荐字段：

```sql
CREATE TABLE knowledge_documents (
  id UUID PRIMARY KEY,
  knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  file_id UUID NOT NULL REFERENCES files(id),
  document_role TEXT NOT NULL DEFAULT 'knowledge_base',
  namespace TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded'
    CHECK (status IN ('uploaded', 'indexing', 'indexed', 'failed', 'deleted')),
  error_message TEXT,
  uploaded_by UUID REFERENCES users(id),
  last_ingested_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);
```

### 11.8 knowledge_index_versions

```sql
CREATE TABLE knowledge_index_versions (
  id UUID PRIMARY KEY,
  knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  namespace TEXT NOT NULL,
  qdrant_collection TEXT NOT NULL,
  qdrant_namespace TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'archived')),
  document_count INTEGER NOT NULL DEFAULT 0,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  embedding_model TEXT,
  manifest_artifact_id UUID,
  error_message TEXT,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ready_at TIMESTAMPTZ
);

CREATE INDEX idx_index_versions_kb_status_created
ON knowledge_index_versions(knowledge_base_id, status, created_at DESC);
```

### 11.9 ingestion_jobs

```sql
CREATE TABLE ingestion_jobs (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
  index_version_id UUID REFERENCES knowledge_index_versions(id),
  job_id UUID,
  mode TEXT NOT NULL DEFAULT 'rebuild_namespace'
    CHECK (mode IN ('rebuild_namespace', 'append_documents', 'delete_document_vectors')),
  namespace TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('created', 'queued', 'running', 'succeeded', 'failed', 'canceled', 'cancel_requested', 'disabled')),
  progress_done INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE INDEX idx_ingestion_jobs_kb_status_created
ON ingestion_jobs(knowledge_base_id, status, created_at DESC);
```

### 11.10 form_files

```sql
CREATE TABLE form_files (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  file_id UUID NOT NULL REFERENCES files(id),
  original_filename TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded',
  uploaded_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_form_files_workspace_created
ON form_files(workspace_id, created_at DESC);
```

### 11.11 fill_runs

需要加强 `created_by` 查询索引、分库关联和 artifact 快捷字段。

```sql
ALTER TABLE fill_runs
ADD COLUMN IF NOT EXISTS knowledge_base_id UUID REFERENCES knowledge_bases(id),
ADD COLUMN IF NOT EXISTS index_version_id UUID REFERENCES knowledge_index_versions(id),
ADD COLUMN IF NOT EXISTS filled_form_artifact_id UUID,
ADD COLUMN IF NOT EXISTS result_summary_artifact_id UUID,
ADD COLUMN IF NOT EXISTS last_event_sequence BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_fill_runs_workspace_created_by_status_created_at
ON fill_runs(workspace_id, created_by, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fill_runs_workspace_status_created_at
ON fill_runs(workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fill_runs_kb_created_at
ON fill_runs(knowledge_base_id, created_at DESC);
```

推荐字段：

```sql
CREATE TABLE fill_runs (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  form_file_id UUID NOT NULL REFERENCES form_files(id),
  knowledge_base_id UUID REFERENCES knowledge_bases(id),
  index_version_id UUID REFERENCES knowledge_index_versions(id),
  job_id UUID,
  target_namespace TEXT NOT NULL,
  global_namespace TEXT,
  room_context TEXT,
  rows TEXT,
  retrieval_mode TEXT,
  prompt_version TEXT,
  judge BOOLEAN NOT NULL DEFAULT false,
  use_judge_cache BOOLEAN NOT NULL DEFAULT false,
  writeback BOOLEAN NOT NULL DEFAULT true,
  status TEXT NOT NULL CHECK (status IN (
    'created', 'queued', 'running', 'succeeded',
    'completed_with_failures', 'failed', 'canceled', 'cancel_requested'
  )),
  progress_done INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  filled_form_artifact_id UUID,
  result_summary_artifact_id UUID,
  error_message TEXT,
  last_event_sequence BIGINT NOT NULL DEFAULT 0,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
```

### 11.12 jobs

```sql
CREATE TABLE jobs (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  run_id UUID,
  job_type TEXT NOT NULL CHECK (job_type IN ('noop', 'ingest_knowledge', 'fill_form', 'archive_artifacts')),
  status TEXT NOT NULL,
  payload JSONB NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  error_message TEXT,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE INDEX idx_jobs_status_created
ON jobs(status, created_at);

CREATE INDEX idx_jobs_workspace_type_status
ON jobs(workspace_id, job_type, status, created_at DESC);
```

### 11.13 run_events

```sql
CREATE TABLE run_events (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  run_id UUID NOT NULL,
  sequence BIGINT NOT NULL,
  event_type TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'info',
  message TEXT NOT NULL,
  progress_done INTEGER,
  progress_total INTEGER,
  payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id, sequence)
);

CREATE INDEX idx_run_events_run_sequence
ON run_events(run_id, sequence);

CREATE INDEX idx_run_events_workspace_created
ON run_events(workspace_id, created_at DESC);
```

### 11.14 run_artifacts

```sql
CREATE TABLE run_artifacts (
  id UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  run_id UUID NOT NULL,
  artifact_kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT,
  size_bytes BIGINT,
  sha256 TEXT,
  storage_bucket TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_run_artifacts_run_kind
ON run_artifacts(run_id, artifact_kind);
```

### 11.15 audit_logs

```sql
CREATE TABLE audit_logs (
  id UUID PRIMARY KEY,
  workspace_id UUID REFERENCES workspaces(id),
  actor_user_id UUID REFERENCES users(id),
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id UUID,
  ip_address INET,
  user_agent TEXT,
  request_id TEXT,
  payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_workspace_created
ON audit_logs(workspace_id, created_at DESC);

CREATE INDEX idx_audit_logs_actor_created
ON audit_logs(actor_user_id, created_at DESC);
```

---

## 12. 数据初始化

### 12.1 默认 workspace

```sql
INSERT INTO workspaces (id, name, description, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  '数据中心工勘平台',
  '默认工作区',
  now(),
  now()
)
ON CONFLICT DO NOTHING;
```

### 12.2 管理员

使用现有 bootstrap admin：

```yaml
auth:
  bootstrap_admin:
    enabled: true
    username: "admin"
    password_env: "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD"
```

### 12.3 固定分库 seed

建议做成 Go 脚本或 migration seed。伪 SQL：

```sql
INSERT INTO knowledge_bases (
  id, workspace_id, name, namespace, description,
  qdrant_collection, status, created_at, updated_at
)
SELECT gen_random_uuid(), w.id, v.name, v.namespace, v.description,
       'datacenter_chunks_v1', 'empty', now(), now()
FROM workspaces w
CROSS JOIN (VALUES
  ('西咸1号楼', 'xixian_1', '西咸园区 1 号楼知识分库'),
  ('西咸2号楼', 'xixian_2', '西咸园区 2 号楼知识分库'),
  ('西咸3号楼', 'xixian_3', '西咸园区 3 号楼知识分库'),
  ('西咸4号楼', 'xixian_4', '西咸园区 4 号楼知识分库'),
  ('西咸5号楼', 'xixian_5', '西咸园区 5 号楼知识分库'),
  ('西咸6号楼', 'xixian_6', '西咸园区 6 号楼知识分库'),
  ('城东浐灞', 'chengdong_chanba', '城东浐灞知识分库'),
  ('西安', 'xian', '西安知识分库'),
  ('咸阳', 'xianyang', '咸阳知识分库')
) AS v(name, namespace, description)
WHERE w.name = '数据中心工勘平台'
ON CONFLICT (workspace_id, namespace) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = now();
```

### 12.4 普通用户

普通用户可以由管理员通过现有用户接口创建，也可以 seed：

```text
username: user
role: operator
workspace member role: operator
```

生产环境不建议写死普通用户密码，应走初始化脚本或管理员页面创建。

---

## 13. 任务状态机

### 13.1 填表任务状态

```text
created -> queued -> running -> succeeded
                         |-> completed_with_failures
                         |-> failed
queued/running -> cancel_requested -> canceled
```

状态含义：

| 状态 | 含义 | 前端表现 |
|---|---|---|
| `created` | 业务记录已创建，尚未入队 | 灰色，短暂存在 |
| `queued` | 已入队，等待 worker | 蓝色，显示“排队中” |
| `running` | Python 正在执行 | 蓝色进度条 |
| `succeeded` | 完整成功，filled_form 可下载 | 完成态，显示主下载按钮 |
| `completed_with_failures` | 有部分字段失败，但有结果 | 完成态，显示下载和风险提示 |
| `failed` | 任务失败，无可用结果或结果不完整 | 红色，显示失败原因 |
| `cancel_requested` | 用户已请求取消 | 黄色，等待 worker 响应 |
| `canceled` | 已取消 | 灰色 |

### 13.2 入库任务状态

```text
created -> queued -> running -> succeeded
                         |-> failed
queued/running -> cancel_requested -> canceled
created/queued -> disabled，当 python.ingest_command_enabled=false
```

### 13.3 进度事件规范

事件类型建议统一：

```text
run.created
run.queued
python.started
python.stdout
python.progress
artifact.detected
artifact.archived
review.imported
run.succeeded
run.completed_with_failures
run.failed
run.cancel_requested
run.canceled
```

事件 payload 示例：

```json
{
  "event_type": "python.progress",
  "message": "正在填写第 37 / 141 行",
  "progress_done": 37,
  "progress_total": 141,
  "payload": {
    "row": 37,
    "field_name": "机柜功率",
    "answer_status": "answered"
  }
}
```

---

## 14. 文件与对象存储设计

### 14.1 存储原则

```text
数据库只存元数据
MinIO/S3 存文件本体
所有下载必须经 Go 鉴权
对象存储 key 不暴露给前端
```

### 14.2 Storage Key 规范

```text
uploads/{workspace_id}/knowledge/{file_id}/{safe_filename}
uploads/{workspace_id}/forms/{file_id}/{safe_filename}
runs/{workspace_id}/{run_id}/artifacts/{artifact_kind}/{filename}
ingestions/{workspace_id}/{ingestion_job_id}/artifacts/{filename}
```

### 14.3 文件校验

知识文档允许：

```text
.xlsx
.xlsx
.docx
.pdf，若 Python 支持
.txt/.md，若 Python 支持
```

工勘单允许：

```text
.xlsx
```

如果允许 `.xlsm`，必须明确是否保留宏。建议初期不执行任何宏，仅作为 Office 文件读取和回写。

### 14.4 下载

下载接口走 Go proxy：

```text
GET /api/v1/files/{file_id}/download
GET /api/v1/fill-runs/{run_id}/download/filled-form
GET /api/v1/artifacts/{artifact_id}/download
```

响应 header：

```text
Content-Disposition: attachment; filename*=UTF-8''...
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Cache-Control: private, no-store
```

---

## 15. 后端配置

### 15.1 关键配置

```yaml
server:
  addr: ":8080"

cors:
  allowed_origins:
    - "http://localhost:5173"
    - "http://localhost:3000"

auth:
  access_token_ttl: "30m"
  refresh_token_ttl: "168h"
  jwt_secret_env: "GONGKAN_JWT_SECRET"
  bootstrap_admin:
    enabled: true
    username: "admin"
    password_env: "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD"

storage:
  type: "minio"
  minio:
    endpoint: "localhost:9000"
    access_key: "minioadmin"
    secret_key: "minioadmin"
    bucket: "gongkan-platform"
    use_ssl: false

python:
  executable: "python"
  project_dir: "../"
  config_path: "config/local.yaml"
  default_timeout: "2h"
  ingest_command_enabled: true
  step15_default_rows: "4-144"
  step15_default_retrieval_mode: "layered"
  step15_default_prompt_version: "step15_compat"
  global_namespace: "global"

jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 1
```

### 15.2 环境变量

```bash
export GONGKAN_JWT_SECRET='replace-with-strong-secret'
export GONGKAN_BOOTSTRAP_ADMIN_PASSWORD='replace-with-admin-password'
export OPENAI_API_KEY='...'
export DASHSCOPE_API_KEY='...'
```

具体 LLM/embedding provider 变量以 Python Core 配置为准。

---

## 16. 部署与启动

### 16.1 依赖服务

完整运行需要：

```text
PostgreSQL
Redis
MinIO/S3
Qdrant
Go API Server
Go Worker
Python Core runtime
Embedding / Rerank / Chat provider
Vue Frontend
```

### 16.2 本地启动命令

```bash
cd go-server
cp configs/config.example.yaml configs/config.local.yaml
make docker-up

export GONGKAN_JWT_SECRET='local-dev-secret-change-me'
export GONGKAN_BOOTSTRAP_ADMIN_PASSWORD='ChangeMe123!'

make run-api CONFIG=configs/config.local.yaml
```

另开终端：

```bash
cd go-server
make run-worker CONFIG=configs/config.local.yaml
```

前端：

```bash
cd web
pnpm install
pnpm dev
```

### 16.3 健康检查

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/api/v1/ping
curl http://localhost:8080/metrics
```

### 16.4 前端环境变量

```env
VITE_API_BASE_URL=http://localhost:8080
VITE_APP_NAME=工勘智能填表
```

---

## 17. 安全设计

### 17.1 鉴权

```text
Access token：短期有效
Refresh token：长期有效，服务端可吊销
所有业务接口需要 BearerAuth
下载接口也需要鉴权
```

### 17.2 前端 token 存储

推荐：

```text
access_token 放内存 + sessionStorage
refresh_token 可放 HttpOnly Cookie；若当前后端未支持 Cookie，则暂存 localStorage 并配合 logout 清理
```

如果后端支持 Cookie Auth，应优先使用 HttpOnly Cookie，降低 XSS 窃取 refresh token 风险。

### 17.3 文件安全

```text
限制文件扩展名
限制 MIME
限制大小
计算 SHA256
对象存储 key 使用 UUID，不直接使用用户文件名
下载时重新设置安全文件名
不执行 Office 宏
```

### 17.4 CORS

```text
只允许前端域名
禁止 '*'
允许 Authorization header
允许 SSE 长连接
```

### 17.5 审计

所有关键写操作和下载操作写 audit logs。

### 17.6 速率限制

```text
登录接口：按 IP + username 限制
上传接口：按用户 + workspace 限制
创建任务接口：按用户限制
SSE：限制单用户并发连接数
```

---

## 18. 可观测性

### 18.1 Metrics

Prometheus 指标：

```text
http_requests_total
http_request_duration_seconds
job_enqueued_total
job_started_total
job_completed_total
job_failed_total
job_duration_seconds
python_process_running
fill_runs_total{status}
ingestion_jobs_total{status}
artifact_archived_total
sse_connections_current
```

避免高基数 label，不把 run_id/user_id 作为 Prometheus label。

### 18.2 日志

结构化日志字段：

```text
request_id
user_id
workspace_id
run_id
job_id
python_pid
event_type
status
error_code
```

敏感字段脱敏：

```text
Authorization
password
refresh_token
DB password
provider API key
```

### 18.3 前端错误上报

前端至少记录：

```text
API 请求失败
SSE 断线次数
下载失败
上传失败
权限跳转
```

可先写 console + 统一 toast；后续接 Sentry 或同类系统。

---

## 19. 关键用户流程

### 19.1 管理员上传知识文档

```text
管理员登录
-> 进入 /admin/knowledge
-> 选择“西咸4号楼”
-> 点击上传文档
-> POST /knowledge-bases/{kb_id}/documents?auto_ingest=true
-> 前端显示上传成功
-> 前端进入入库进度面板
-> SSE 显示 parsing / embedding / qdrant upsert
-> 成功后分库状态变 ready
```

### 19.2 管理员删除知识文档

```text
管理员进入分库
-> 点击文档删除
-> 确认弹窗说明：删除后将刷新索引
-> DELETE /documents/{doc_id}?reindex=true
-> 分库状态变 stale/building
-> Worker 重建 namespace
-> 成功后 ready
```

### 19.3 普通用户自动填表

```text
用户登录
-> /fill
-> 获取 knowledge options
-> 选择 ready 分库
-> 上传工勘单
-> 输入 room_context
-> 点击开始填写
-> POST /fill-runs/simple
-> 跳转 /fill/runs/{run_id}
-> SSE 显示进度
-> 成功后下载 filled_form.xlsx
```

### 19.4 用户退出后回来查看任务

```text
用户关闭浏览器
-> Go Worker 继续执行 Python
-> 用户重新登录
-> /fill/history
-> GET /fill-runs?mine=true
-> 看到 queued/running/succeeded 任务
-> 点击详情继续接 SSE 或下载结果
```

---

## 20. 验收标准

### 20.1 登录与权限

```text
管理员能登录
普通用户能登录
管理员能看到知识库管理入口
普通用户看不到知识库管理入口
普通用户直接访问 /admin/knowledge 会跳转 /fill
普通用户直接请求知识库写接口返回 403
普通用户不能查看或下载其他用户的填表任务结果
```

### 20.2 知识库管理

```text
管理员能看到 9 个固定分库
每个分库展示 name、namespace、状态、文档数
管理员能查看分库文档列表
管理员能下载知识文档
管理员能上传知识文档
上传后能自动创建入库任务
入库过程能看到实时事件
入库成功后分库 ready
管理员能删除知识文档
删除后旧文档不再参与检索
删除后分库索引能刷新到 ready
```

### 20.3 自动填表

```text
普通用户能看到 ready 分库 options
非 ready 分库不可选
用户能上传 .xlsx 工勘单
用户能创建填表任务
浏览器退出后任务继续执行
重新登录后能看到自己的任务
任务详情页显示实时进度
任务成功后能下载 filled_form.xlsx
任务失败时显示失败原因
取消任务后状态变 canceled
```

### 20.4 任务与并发

```text
配置 fill_concurrency=1 后，同一时间只有一个填表 Python 进程运行
多个任务提交后按队列执行
SSE 断线后可用 after_sequence 补事件
Worker 重启后能从数据库状态恢复未完成任务或标记失败
```

### 20.5 视觉验收

```text
页面没有传统厚重后台侧边栏
GlobalNav 为 44px 黑色
SubNav 为 52px 半透明米白毛玻璃
主按钮为蓝色 pill
卡片为白底、18px 圆角、1px hairline、无厚重阴影
主要页面留白充分
分库选择是卡片/pill，而不是复杂表格
任务进度以清晰时间线呈现
错误和警告克制展示，不破坏整体视觉
```

---

## 21. 开发交付清单

这里不按“第一版/第二版”拆分，而按最终完整系统需要的交付物列出。

### 21.1 前端交付物

```text
Vue 3 + Vite + TypeScript 工程
Apple-inspired DESIGN tokens
Element Plus 主题覆写
Axios API 层
Pinia auth/workspace/knowledge/fillRun store
Router guard
LoginView
AppLayout / GlobalNav / SubNav
FillCreateView
FillHistoryView
FillRunDetailView
AdminKnowledgeView
UploadDropzone
KnowledgeBaseSelector
KnowledgeDocumentTable
RunProgressTimeline
ArtifactDownloadPanel
SSE 订阅与断线重连
带鉴权的文件下载工具
统一错误 toast/dialog
```

### 21.2 后端交付物

```text
knowledge_bases.namespace migration
knowledge_bases.status/document_count/last_ingested_at 字段
固定 9 分库 seed
GET /knowledge-bases/options
POST /fill-runs/simple
GET /fill-runs 支持 mine=true
普通用户 fill_run owner 权限收紧
知识库写接口 admin 权限收紧
上传知识文档 auto_ingest=true
删除知识文档 reindex=true
run_events.sequence 严格递增
artifact 下载权限反查 run ownership
配置 fill_concurrency=1 / max_python_processes=1
审计日志补齐
OpenAPI 更新
```

### 21.3 数据库交付物

```text
knowledge_bases namespace/status/document_count/last_ingested_at
knowledge_documents deleted_at/last_ingested_at
fill_runs knowledge_base_id/index_version_id/filled_form_artifact_id/last_event_sequence
run_events sequence unique(run_id, sequence)
必要索引
固定分库 seed
默认 workspace seed
管理员 bootstrap
```

### 21.4 运维交付物

```text
Docker Compose 启动 Postgres/Redis/MinIO/Qdrant
Go API 启动脚本
Go Worker 启动脚本
前端环境变量样例
健康检查命令
Smoke test 脚本
备份策略：Postgres + MinIO bucket
日志脱敏
Prometheus metrics
```

---

## 22. 设计决策摘要

1. **分库必须进入数据库模型，而不是前端硬编码。** 中文展示名和 RAG namespace 一一绑定，前端只选择 knowledge_base_id。
2. **结果文件不直接存 PostgreSQL。** PostgreSQL 存 metadata，MinIO/S3 存 filled_form.xlsx，下载经 Go 鉴权。
3. **普通用户只能看自己的填表任务。** workspace 权限不足以表达任务所有权，必须加 created_by 约束。
4. **知识库管理必须后端强制 admin。** 前端路由守卫只做体验，不做安全边界。
5. **填写任务由 Worker 执行。** 浏览器退出不会影响后端任务。
6. **如需固定一个填表进程，用 worker 并发配置和资源限制实现。** 不用前端控制。
7. **文档删除必须处理 Qdrant 一致性。** 否则旧知识仍可能被检索。
8. **前端暴露产品语义，不暴露实验参数。** 用户看到分库、文件、房间上下文；`target_namespace`、`rows`、`prompt_version` 由后端派生。
9. **Apple 风格应用在信息架构和视觉克制上。** 不是复制 Apple 品牌，而是采用高留白、系统字体、单一蓝色、pill 控件和轻量卡片。
10. **SSE 使用 fetch-event-source。** 既支持 Authorization header，也支持断线后基于 sequence 恢复。

---

## 23. 参考依据

- VoltAgent `awesome-design-md` 仓库：Apple DESIGN.md 条目描述为 premium white space、SF Pro、cinematic imagery，并说明 DESIGN.md 是用于 AI/coding agents 的纯 Markdown 设计系统文档。
- `nested-doc-rag` Go 后端 README：Go 后端是 Python Core 外围工业平台层，负责 API、auth、storage、jobs、orchestration、review、download、observability；已有 Block 5/6/7/8 能力。
- Go 后端详细设计文档：系统边界为 Frontend -> Go API Server -> PostgreSQL/Redis/MinIO -> Go Worker -> Python Core CLI -> Qdrant/Embedding/Rerank/Chat Services。
- Go OpenAPI：已有 auth、users、workspaces、files、forms、fill-runs、run events、artifacts、knowledge-bases、documents、ingestion-runs、review-items、result center 等接口。
