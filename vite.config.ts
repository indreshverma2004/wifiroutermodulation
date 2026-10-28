import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig(({mode})=>{
  // Match the documented Windows fallback port used when 8000 is reserved.
  const port=loadEnv(mode,'.','VITE_').VITE_BACKEND_PORT||'8001'
  const target=`127.0.0.1:${port}`
  return {plugins:[react()],server:{proxy:{'/api':`http://${target}`,'/ws':{target:`ws://${target}`,ws:true}}}}
})
