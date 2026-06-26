<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import AuthLayout from '@/layouts/AuthLayout.vue'
import { useAuthStore } from '@/stores/auth.store'
import { API_BASE } from '@/api/http'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')

async function submit() {
  try {
    await auth.login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    if (redirect) {
      router.push(redirect)
    } else {
      router.push({ name: 'fill-create' })
    }
  } catch {
    ElMessage.error('账号或密码错误，或后端服务不可用')
  }
}
</script>

<template>
  <AuthLayout>
    <section class="login-card">
      <h1>工勘智能填表</h1>
      <p>选择知识分库，上传工勘单，自动生成回填结果。</p>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-button type="primary" :loading="auth.loading" class="login-card__button" @click="submit">登录</el-button>
      </el-form>
      <div class="gk-caption">API: {{ API_BASE }}</div>
    </section>
  </AuthLayout>
</template>

<style scoped>
.login-card {
  width: min(420px, calc(100vw - 32px));
  padding: 34px;
  border: 1px solid var(--gk-glass-line);
  border-radius: var(--gk-radius-lg);
  background: var(--gk-glass-fallback);
  box-shadow: var(--gk-glass-shadow);
  position: relative;
  overflow: hidden;
}

.login-card::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.54), transparent 38%);
}

.login-card > * {
  position: relative;
}

.login-card h1 {
  margin: 0 0 8px;
  font-size: 32px;
  font-weight: 600;
}

.login-card p {
  margin: 0 0 28px;
  color: var(--gk-ink-3);
  line-height: 1.5;
}

.login-card__button {
  width: 100%;
  margin: 8px 0 18px;
}

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .login-card {
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.78), rgba(255, 255, 255, 0.52));
    border-color: var(--gk-glass-border);
    -webkit-backdrop-filter: saturate(var(--gk-glass-saturate)) blur(var(--gk-glass-blur));
    backdrop-filter: saturate(var(--gk-glass-saturate)) blur(var(--gk-glass-blur));
  }
}
</style>
