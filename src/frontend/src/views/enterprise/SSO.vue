<script setup>
/**
 * Enterprise SSO admin view (#847 PoC).
 *
 * Phase 0 scope: mock UI. Renders provider rows, claim-mapping
 * table, session-policy panel. All action buttons disabled with
 * tooltips pointing at issue #847 — no real OIDC/SAML implementation
 * yet. The backend's `GET /providers`, `GET /claim-mapping`,
 * `GET /session-policy` seed realistic data (Okta + Azure AD).
 *
 * Route gate: `meta.requiresEntitlement: 'sso'` in router.js. NavBar
 * hides the Enterprise link in OSS-only builds; this view also
 * tolerates a stale direct visit by rendering the empty / error
 * state cleanly when the backend returns 404 (no submodule).
 */
import { ref, onMounted, computed } from 'vue'
import api from '../../api'

const providers = ref([])
const claimMapping = ref([])
const sessionPolicy = ref({
  force_sso_only: false,
  session_lifetime_hours: 8,
  require_sso_reauth_for_admin: false,
})
const loading = ref(true)
const error = ref(null)
const showAddModal = ref(false)
const newProvider = ref({
  protocol: 'oidc',
  display_name: '',
  provider_id: '',
  issuer_url: '',
  client_id: '',
  client_secret: '',
  scopes: 'openid email profile groups',
  enabled: true,
})

const callbackUrl = computed(() => {
  const id = newProvider.value.provider_id || '<id>'
  return `${window.location.origin}/api/enterprise/sso/callback/${id}`
})

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const [providersRes, mappingRes, policyRes] = await Promise.all([
      api.get('/api/enterprise/sso/providers'),
      api.get('/api/enterprise/sso/claim-mapping').catch(() => ({ data: [] })),
      api.get('/api/enterprise/sso/session-policy').catch(() => ({ data: sessionPolicy.value })),
    ])
    providers.value = providersRes.data || []
    claimMapping.value = mappingRes.data || []
    if (policyRes.data) sessionPolicy.value = policyRes.data
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)

function protocolBadge(protocol) {
  return protocol === 'oidc'
    ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200'
    : 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-200'
}

function copyCallback() {
  navigator.clipboard?.writeText(callbackUrl.value)
}

function providerIcon(p) {
  // Quick visual sugar — real impl would ship per-provider SVG assets.
  if (p.provider_id.startsWith('okta')) return '🅾'
  if (p.provider_id.startsWith('azure')) return 'Ⓜ'
  if (p.provider_id.startsWith('google')) return 'Ⓖ'
  return '🔐'
}

function formatLastLogin(iso) {
  if (!iso) return 'never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const TOOLTIP_DISABLED = 'PoC stub (#847) — no real OIDC/SAML implementation yet'
</script>

<template>
  <div class="enterprise-sso p-6 max-w-5xl mx-auto">
    <header class="mb-6 flex items-center justify-between">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-2xl font-semibold text-gray-900 dark:text-white">Single Sign-On</h1>
          <span class="px-2 py-0.5 text-xs font-bold rounded bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-200">PRO</span>
        </div>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Configure SAML 2.0 and OIDC identity providers. Issue
          <a href="https://github.com/abilityai/trinity/issues/847" class="underline" target="_blank">#847</a>
          — PoC: UI complete, login flow not yet wired.
        </p>
      </div>
      <button
        @click="showAddModal = true"
        :title="TOOLTIP_DISABLED"
        class="px-4 py-2 text-sm font-medium rounded bg-blue-500 text-white opacity-90 hover:opacity-100"
      >
        + Add provider
      </button>
    </header>

    <div v-if="loading" class="text-gray-500 text-sm">Loading…</div>

    <div v-else-if="error" class="bg-red-50 border border-red-200 rounded p-4 text-sm text-red-700">
      <strong class="block">Failed to load SSO configuration</strong>
      <span>{{ error }}</span>
    </div>

    <template v-else>
      <!-- =================== PROVIDERS LIST =================== -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-3">
          Configured providers ({{ providers.length }})
        </h2>
        <div v-if="providers.length === 0" class="bg-gray-50 border border-gray-200 rounded p-6 text-center">
          <p class="text-gray-600 mb-2">No SSO providers configured.</p>
          <p class="text-xs text-gray-400">Click "Add provider" to configure your first IdP.</p>
        </div>
        <ul v-else class="space-y-2">
          <li
            v-for="p in providers"
            :key="p.provider_id"
            class="border rounded p-4 bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700"
          >
            <div class="flex items-start justify-between">
              <div class="flex items-start gap-3">
                <span class="text-2xl mt-0.5">{{ providerIcon(p) }}</span>
                <div>
                  <div class="flex items-center gap-2 mb-1">
                    <span class="font-medium text-gray-900 dark:text-white">{{ p.display_name }}</span>
                    <span class="text-xs px-1.5 py-0.5 rounded font-mono uppercase" :class="protocolBadge(p.protocol)">
                      {{ p.protocol }}
                    </span>
                    <span
                      class="text-xs px-1.5 py-0.5 rounded font-medium"
                      :class="p.enabled
                        ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200'
                        : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'"
                    >
                      {{ p.enabled ? '● Active' : '○ Disabled' }}
                    </span>
                  </div>
                  <div class="text-xs text-gray-500 dark:text-gray-400 font-mono">{{ p.provider_id }}</div>
                  <div v-if="p.issuer_or_metadata_url" class="text-xs text-gray-400 dark:text-gray-500 mt-1 truncate max-w-md">
                    {{ p.issuer_or_metadata_url }}
                  </div>
                  <div class="text-xs text-gray-400 dark:text-gray-500 mt-1">
                    Last login: {{ formatLastLogin(p.last_login_at) }}
                  </div>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <button :title="TOOLTIP_DISABLED" disabled class="px-3 py-1 text-xs border rounded text-gray-500 cursor-not-allowed">Test</button>
                <button :title="TOOLTIP_DISABLED" disabled class="px-3 py-1 text-xs border rounded text-gray-500 cursor-not-allowed">Edit</button>
                <button :title="TOOLTIP_DISABLED" disabled class="px-2 py-1 text-xs text-gray-400 cursor-not-allowed">⋮</button>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <!-- =================== CLAIM MAPPING =================== -->
      <section class="mb-8">
        <h2 class="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-3">
          Identity → Role Mapping
        </h2>
        <div class="border rounded bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-gray-50 dark:bg-gray-900 text-xs text-gray-500 dark:text-gray-400">
              <tr>
                <th class="px-4 py-2 text-left font-medium">IdP claim/group</th>
                <th class="px-4 py-2 text-left font-medium">Trinity role</th>
                <th class="px-4 py-2 w-32"></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="rule in claimMapping"
                :key="rule.claim_value"
                class="border-t border-gray-200 dark:border-gray-700"
                :class="{ 'bg-gray-50 dark:bg-gray-900 italic': rule.is_fallback }"
              >
                <td class="px-4 py-2 font-mono text-xs text-gray-700 dark:text-gray-300">{{ rule.claim_value }}</td>
                <td class="px-4 py-2">
                  <span class="px-2 py-0.5 text-xs rounded font-mono bg-purple-50 text-purple-700 dark:bg-purple-900 dark:text-purple-200">
                    {{ rule.trinity_role }}
                  </span>
                </td>
                <td class="px-4 py-2 text-right">
                  <button v-if="!rule.is_fallback" :title="TOOLTIP_DISABLED" disabled class="text-xs text-gray-400 cursor-not-allowed mr-2">Edit</button>
                  <button v-if="!rule.is_fallback" :title="TOOLTIP_DISABLED" disabled class="text-xs text-gray-400 cursor-not-allowed">Delete</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="px-4 py-2 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-right">
            <button :title="TOOLTIP_DISABLED" disabled class="text-xs text-blue-500 opacity-50 cursor-not-allowed">+ Add rule</button>
          </div>
        </div>
      </section>

      <!-- =================== SESSION POLICY =================== -->
      <section>
        <h2 class="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-3">
          Session Policy
        </h2>
        <div class="border rounded bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 p-4 space-y-3">
          <label class="flex items-center gap-2 cursor-not-allowed">
            <input
              type="checkbox"
              :checked="sessionPolicy.force_sso_only"
              disabled
              :title="TOOLTIP_DISABLED"
              class="rounded"
            />
            <span class="text-sm text-gray-700 dark:text-gray-300">
              Force SSO-only (disable email/password login for all non-admin users)
            </span>
          </label>

          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-700 dark:text-gray-300">Session lifetime:</span>
            <select
              :value="sessionPolicy.session_lifetime_hours"
              disabled
              :title="TOOLTIP_DISABLED"
              class="px-2 py-1 text-sm border rounded bg-gray-50 dark:bg-gray-900 cursor-not-allowed"
            >
              <option :value="sessionPolicy.session_lifetime_hours">{{ sessionPolicy.session_lifetime_hours }} hours</option>
            </select>
          </div>

          <label class="flex items-center gap-2 cursor-not-allowed">
            <input
              type="checkbox"
              :checked="sessionPolicy.require_sso_reauth_for_admin"
              disabled
              :title="TOOLTIP_DISABLED"
              class="rounded"
            />
            <span class="text-sm text-gray-700 dark:text-gray-300">
              Require SSO re-authentication for admin actions
            </span>
          </label>
        </div>
      </section>
    </template>

    <!-- =================== ADD PROVIDER MODAL (stub) =================== -->
    <div v-if="showAddModal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div class="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-lg w-full p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-semibold text-gray-900 dark:text-white">Add SSO provider</h3>
          <button @click="showAddModal = false" class="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div class="space-y-3">
          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Provider type</label>
            <div class="flex gap-4">
              <label class="flex items-center gap-2">
                <input type="radio" v-model="newProvider.protocol" value="oidc" />
                <span class="text-sm">OIDC</span>
              </label>
              <label class="flex items-center gap-2">
                <input type="radio" v-model="newProvider.protocol" value="saml" />
                <span class="text-sm">SAML 2.0</span>
              </label>
            </div>
          </div>

          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Display name</label>
            <input v-model="newProvider.display_name" placeholder="Okta production" class="w-full px-3 py-2 border rounded text-sm bg-white dark:bg-gray-900" />
          </div>

          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
              Provider ID
              <span class="text-gray-400 font-normal">(URL-safe; used in callback URL)</span>
            </label>
            <input v-model="newProvider.provider_id" placeholder="okta-prod" class="w-full px-3 py-2 border rounded text-sm font-mono bg-white dark:bg-gray-900" />
          </div>

          <div class="border-t border-gray-200 dark:border-gray-700 pt-3">
            <p class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2 uppercase">{{ newProvider.protocol }} configuration</p>

            <div class="space-y-3">
              <div>
                <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Issuer URL</label>
                <input v-model="newProvider.issuer_url" placeholder="https://acme.okta.com" class="w-full px-3 py-2 border rounded text-sm bg-white dark:bg-gray-900" />
              </div>
              <div>
                <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Client ID</label>
                <input v-model="newProvider.client_id" placeholder="0oa1234567890abcdef" class="w-full px-3 py-2 border rounded text-sm font-mono bg-white dark:bg-gray-900" />
              </div>
              <div>
                <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Client secret</label>
                <input v-model="newProvider.client_secret" type="password" placeholder="••••••••••••••" class="w-full px-3 py-2 border rounded text-sm font-mono bg-white dark:bg-gray-900" />
              </div>
              <div>
                <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Scopes</label>
                <input v-model="newProvider.scopes" class="w-full px-3 py-2 border rounded text-sm font-mono bg-white dark:bg-gray-900" />
              </div>
            </div>
          </div>

          <div class="border-t border-gray-200 dark:border-gray-700 pt-3">
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
              Callback URL <span class="text-gray-400 font-normal">(copy to IdP)</span>
            </label>
            <div class="flex gap-2">
              <input :value="callbackUrl" readonly class="flex-1 px-3 py-2 border rounded text-xs font-mono bg-gray-50 dark:bg-gray-900" />
              <button @click="copyCallback" class="px-3 py-1 text-xs border rounded hover:bg-gray-50 dark:hover:bg-gray-700">Copy</button>
            </div>
          </div>

          <label class="flex items-center gap-2 pt-2">
            <input type="checkbox" v-model="newProvider.enabled" />
            <span class="text-sm text-gray-700 dark:text-gray-300">Enabled on save</span>
          </label>
        </div>

        <div class="flex justify-end gap-2 mt-6 pt-4 border-t border-gray-200 dark:border-gray-700">
          <button @click="showAddModal = false" class="px-4 py-2 text-sm border rounded">Cancel</button>
          <button :title="TOOLTIP_DISABLED" disabled class="px-4 py-2 text-sm bg-blue-500 text-white rounded opacity-50 cursor-not-allowed">
            Save + Test login
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
