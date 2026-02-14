<script setup lang="ts">
import { ref, onMounted } from 'vue'
import Card from 'primevue/card'
import Dropdown from 'primevue/dropdown'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Tag from 'primevue/tag'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import { defStoreClient } from '@/api/client'
import { useTerminologyStore, useUiStore } from '@/stores'
import type { Terminology, ValidateValueResponse, BulkValidateResponse } from '@/types'

const terminologyStore = useTerminologyStore()
const uiStore = useUiStore()

const selectedTerminology = ref<Terminology | null>(null)
const singleValue = ref('')
const bulkValues = ref('')
const validating = ref(false)

const singleResult = ref<ValidateValueResponse | null>(null)
const bulkResult = ref<BulkValidateResponse | null>(null)

onMounted(async () => {
  try {
    await terminologyStore.fetchTerminologies()
  } catch (e) {
    // Error handled by store
  }
})

async function validateSingle() {
  if (!selectedTerminology.value || !singleValue.value.trim()) {
    uiStore.showWarn('Missing Input', 'Select a terminology and enter a value')
    return
  }

  validating.value = true
  singleResult.value = null
  try {
    singleResult.value = await defStoreClient.validateValue({
      terminology_id: selectedTerminology.value.terminology_id,
      value: singleValue.value.trim()
    })
  } catch (e) {
    uiStore.showError('Validation Failed', (e as Error).message)
  } finally {
    validating.value = false
  }
}

async function validateBulk() {
  if (!selectedTerminology.value || !bulkValues.value.trim()) {
    uiStore.showWarn('Missing Input', 'Select a terminology and enter values')
    return
  }

  const values = bulkValues.value
    .split('\n')
    .map(v => v.trim())
    .filter(v => v.length > 0)

  if (values.length === 0) {
    uiStore.showWarn('No Values', 'Enter at least one value to validate')
    return
  }

  validating.value = true
  bulkResult.value = null
  try {
    bulkResult.value = await defStoreClient.bulkValidate({
      items: values.map(value => ({
        terminology_id: selectedTerminology.value!.terminology_id,
        value
      }))
    })
    uiStore.showInfo('Validation Complete', `${bulkResult.value.valid_count}/${bulkResult.value.total} valid`)
  } catch (e) {
    uiStore.showError('Validation Failed', (e as Error).message)
  } finally {
    validating.value = false
  }
}

function clearSingle() {
  singleValue.value = ''
  singleResult.value = null
}

function clearBulk() {
  bulkValues.value = ''
  bulkResult.value = null
}
</script>

<template>
  <div class="validate-view">
    <h1>Validate Values</h1>
    <p class="subtitle">Check if values are valid for a terminology</p>

    <Card class="terminology-select-card">
      <template #content>
        <div class="select-row">
          <label for="terminology">Select Terminology</label>
          <Dropdown
            id="terminology"
            v-model="selectedTerminology"
            :options="terminologyStore.terminologies"
            option-label="label"
            placeholder="Choose a terminology..."
            class="terminology-dropdown"
            filter
          >
            <template #option="{ option }">
              <div class="terminology-option">
                <span class="code-badge">{{ option.value }}</span>
                <span>{{ option.label }}</span>
                <span class="term-count">({{ option.term_count }} terms)</span>
              </div>
            </template>
          </Dropdown>
        </div>
      </template>
    </Card>

    <TabView v-if="selectedTerminology">
      <TabPanel value="0" header="Single Value">
        <Card>
          <template #content>
            <div class="single-validate">
              <div class="input-row">
                <InputText
                  v-model="singleValue"
                  placeholder="Enter a value to validate..."
                  class="value-input"
                  @keyup.enter="validateSingle"
                />
                <Button
                  label="Validate"
                  icon="pi pi-check"
                  :loading="validating"
                  @click="validateSingle"
                />
                <Button
                  icon="pi pi-times"
                  severity="secondary"
                  text
                  @click="clearSingle"
                />
              </div>

              <div v-if="singleResult" class="single-result">
                <div :class="['result-badge', singleResult.valid ? 'valid' : 'invalid']">
                  <i :class="singleResult.valid ? 'pi pi-check-circle' : 'pi pi-times-circle'" />
                  <span>{{ singleResult.valid ? 'Valid' : 'Invalid' }}</span>
                </div>

                <div v-if="singleResult.matched_term" class="matched-term">
                  <h4>Matched Term</h4>
                  <div class="term-details">
                    <div class="detail-row">
                      <span class="label">Value:</span>
                      <span class="code-badge">{{ singleResult.matched_term.value }}</span>
                    </div>
                    <div class="detail-row">
                      <span class="label">Label:</span>
                      <span>{{ singleResult.matched_term.label }}</span>
                    </div>
                    <div v-if="singleResult.matched_term.description" class="detail-row">
                      <span class="label">Description:</span>
                      <span>{{ singleResult.matched_term.description }}</span>
                    </div>
                  </div>
                </div>

                <div v-if="!singleResult.valid && singleResult.suggestion" class="suggestion">
                  <h4>Did you mean?</h4>
                  <div class="suggestion-term">
                    <span class="code-badge">{{ singleResult.suggestion.value }}</span>
                    <span>{{ singleResult.suggestion.label }}</span>
                    <span class="value-text">({{ singleResult.suggestion.value }})</span>
                  </div>
                </div>

                <div v-if="singleResult.error" class="error-message">
                  {{ singleResult.error }}
                </div>
              </div>
            </div>
          </template>
        </Card>
      </TabPanel>

      <TabPanel value="1" header="Bulk Validation">
        <Card>
          <template #content>
            <div class="bulk-validate">
              <div class="input-section">
                <label for="bulk-values">Enter values (one per line)</label>
                <Textarea
                  id="bulk-values"
                  v-model="bulkValues"
                  rows="8"
                  placeholder="value1&#10;value2&#10;value3"
                  class="bulk-input"
                />
                <div class="bulk-actions">
                  <Button
                    label="Validate All"
                    icon="pi pi-check"
                    :loading="validating"
                    @click="validateBulk"
                  />
                  <Button
                    label="Clear"
                    severity="secondary"
                    text
                    @click="clearBulk"
                  />
                </div>
              </div>

              <div v-if="bulkResult" class="bulk-result">
                <div class="result-summary">
                  <div class="summary-item">
                    <span class="summary-value valid">{{ bulkResult.valid_count }}</span>
                    <span class="summary-label">Valid</span>
                  </div>
                  <div class="summary-item">
                    <span class="summary-value invalid">{{ bulkResult.invalid_count }}</span>
                    <span class="summary-label">Invalid</span>
                  </div>
                  <div class="summary-item">
                    <span class="summary-value">{{ bulkResult.total }}</span>
                    <span class="summary-label">Total</span>
                  </div>
                </div>

                <DataTable :value="bulkResult.results" striped-rows size="small" paginator :rows="10">
                  <Column field="value" header="Value" style="width: 30%">
                    <template #body="{ data }">
                      <code>{{ data.value }}</code>
                    </template>
                  </Column>
                  <Column header="Status" style="width: 15%">
                    <template #body="{ data }">
                      <Tag
                        :value="data.valid ? 'Valid' : 'Invalid'"
                        :severity="data.valid ? 'success' : 'danger'"
                      />
                    </template>
                  </Column>
                  <Column header="Matched Term" style="width: 30%">
                    <template #body="{ data }">
                      <span v-if="data.matched_term">
                        {{ data.matched_term.label }} ({{ data.matched_term.value }})
                      </span>
                      <span v-else class="no-match">-</span>
                    </template>
                  </Column>
                  <Column header="Suggestion" style="width: 25%">
                    <template #body="{ data }">
                      <span v-if="data.suggestion" class="suggestion-text">
                        {{ data.suggestion.value }}
                      </span>
                      <span v-else>-</span>
                    </template>
                  </Column>
                </DataTable>
              </div>
            </div>
          </template>
        </Card>
      </TabPanel>
    </TabView>

    <div v-else class="no-selection">
      <i class="pi pi-info-circle"></i>
      <p>Select a terminology to start validating values</p>
    </div>
  </div>
</template>

<style scoped>
.validate-view {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 1000px;
}

.validate-view h1 {
  margin: 0;
}

.subtitle {
  margin: 0;
  color: var(--p-text-muted-color);
}

.select-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.select-row label {
  font-weight: 500;
  white-space: nowrap;
}

.terminology-dropdown {
  flex: 1;
  max-width: 400px;
}

.terminology-option {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.code-badge {
  font-family: monospace;
  background: var(--p-surface-100);
  padding: 0.125rem 0.375rem;
  border-radius: 3px;
  font-size: 0.8rem;
}

.term-count {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.single-validate {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.input-row {
  display: flex;
  gap: 0.5rem;
}

.value-input {
  flex: 1;
}

.single-result {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem;
  background: var(--p-surface-50);
  border-radius: 6px;
}

.result-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  font-size: 1.1rem;
}

.result-badge.valid {
  color: var(--p-green-500);
}

.result-badge.invalid {
  color: var(--p-red-500);
}

.result-badge i {
  font-size: 1.25rem;
}

.matched-term h4,
.suggestion h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.term-details {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.detail-row {
  display: flex;
  gap: 0.5rem;
}

.detail-row .label {
  color: var(--p-text-muted-color);
  min-width: 80px;
}

.suggestion-term {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.value-text {
  font-family: monospace;
  color: var(--p-text-muted-color);
}

.error-message {
  color: var(--p-red-500);
  font-size: 0.875rem;
}

.bulk-validate {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.input-section {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.input-section label {
  font-weight: 500;
}

.bulk-input {
  width: 100%;
  font-family: monospace;
}

.bulk-actions {
  display: flex;
  gap: 0.5rem;
}

.bulk-result {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.result-summary {
  display: flex;
  gap: 2rem;
}

.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.summary-value {
  font-size: 1.5rem;
  font-weight: 600;
}

.summary-value.valid {
  color: var(--p-green-500);
}

.summary-value.invalid {
  color: var(--p-red-500);
}

.summary-label {
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}

.no-match {
  color: var(--p-text-muted-color);
}

.suggestion-text {
  color: var(--p-primary-color);
  font-family: monospace;
}

.no-selection {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 3rem;
  color: var(--p-text-muted-color);
  text-align: center;
}

.no-selection i {
  font-size: 2rem;
  opacity: 0.5;
}
</style>
