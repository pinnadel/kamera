// ModelStatusView — full-screen "your taste model" view. Shows the
// growth tier, decision count, and a Retrain action.

import { Sparkles, TrendingUp } from 'lucide-react'
import { TopBar } from '../components/TopBar'
import { ModelBanner } from '../components/ModelBanner'
import { MobileInfo } from '../components/MobileInfo'

export function ModelStatusView({ modelState, addToast, back }) {
  const info = modelState?.info
  const train = modelState?.train

  const onRetrain = async () => {
    const result = await train?.()
    if (result) {
      addToast({
        type: 'success',
        message: `Model retrained on ${result.training_size || 0} decisions`,
      })
    } else {
      addToast({ type: 'error', message: 'Training failed' })
    }
  }

  return (
    <>
      <TopBar title="Your taste model" subtitle="Personal scoring" onBack={back} />

      <main className="flex-1 overflow-y-auto pb-12 px-4">
        <div className="pt-4">
          <ModelBanner info={info} onOpen={() => {}} />
        </div>

        <section className="mt-5">
          <div className="flex items-center gap-2 mb-2">
            <h2 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d]">How it works</h2>
            <MobileInfo
              icon
              label="About the personal model"
              content={
                <p className="text-[13px] leading-snug">
                  A gradient-boosted regressor trained on the 17-feature vector of every decision you make. Predicts a delta from the technical score; the predicted personal score is shown as the indigo bar across the app.
                </p>
              }
            />
          </div>
          <div className="rounded-2xl p-4 bg-[#101111] border border-white/5 space-y-3 text-[14px] text-[#cecece] leading-relaxed">
            <p className="flex items-start gap-3">
              <Sparkles size={16} className="text-[#5BB8D4] mt-0.5" aria-hidden="true" />
              <span>Every K / M / R decision feeds the model. After 30 decisions it begins predicting; after 50 it can drive Auto-cull.</span>
            </p>
            <p className="flex items-start gap-3">
              <TrendingUp size={16} className="text-[#5BB8D4] mt-0.5" aria-hidden="true" />
              <span>Recent decisions are weighted heavier (180-day half-life), so as your taste evolves the model keeps up.</span>
            </p>
          </div>
        </section>

        {info && (
          <section className="mt-4">
            <h2 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d] mb-2">Stats</h2>
            <dl className="rounded-2xl bg-[#101111] border border-white/5 overflow-hidden">
              <Row label="Status"          value={info.model_status || (info.ready ? 'ready' : 'untrained')} />
              <Row label="Training size"   value={`${info.training_size ?? 0} decisions`} />
              {info.r2 != null && <Row label="R²" value={info.r2.toFixed(3)} />}
              {info.last_trained_at && <Row label="Last trained" value={new Date(info.last_trained_at).toLocaleString()} />}
              {info.feature_schema_version != null && <Row label="Feature schema" value={`v${info.feature_schema_version}`} />}
            </dl>
          </section>
        )}

        <section className="mt-5">
          <button
            type="button"
            onClick={onRetrain}
            disabled={modelState?.training}
            className="m-btn m-btn-primary w-full disabled:opacity-50"
          >
            <Sparkles size={16} aria-hidden="true" />
            {modelState?.training ? 'Training…' : 'Retrain now'}
          </button>
          <p className="text-[12px] text-[#9c9c9d] mt-2 leading-snug">
            Auto-trains in the background after each decision. Manual retrain is rarely needed but doesn't hurt.
          </p>
        </section>
      </main>
    </>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-white/5 last:border-0">
      <dt className="text-[13px] text-[#9c9c9d] w-32">{label}</dt>
      <dd className="text-[14px] text-[#f9f9f9] font-mono m-tabular flex-1 truncate">{value}</dd>
    </div>
  )
}
