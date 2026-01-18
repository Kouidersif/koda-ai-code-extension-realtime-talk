import { EventEmitter } from 'events';

export class GeminiLiveClient extends EventEmitter {
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 5;
    private reconnectDelay = 2000;

    async connect(url: string): Promise<void> {
        return new Promise((resolve, reject) => {
            try {
                this.ws = new WebSocket(url);

                this.ws.onopen = () => {
                    console.log('Connected to Gemini Live backend');
                    this.reconnectAttempts = 0;
                    resolve();
                };

                this.ws.onmessage = (event) => {
                    this.handleMessage(event);
                };

                this.ws.onerror = (error) => {
                    console.error('WebSocket error:', error);
                    this.emit('error', 'WebSocket connection error');
                    reject(error);
                };

                this.ws.onclose = () => {
                    console.log('WebSocket closed');
                    this.emit('disconnected');
                    this.attemptReconnect(url);
                };
            } catch (error) {
                reject(error);
            }
        });
    }

    private async handleMessage(event: MessageEvent) {
        try {
            if (event.data instanceof Blob) {
                // Audio data from Gemini
                const audioData = await event.data.arrayBuffer();
                this.emit('audio', new Uint8Array(audioData));
            } else if (typeof event.data === 'string') {
                try {
                    const message = JSON.parse(event.data);
                    this.processEvent(message);
                } catch (error) {
                    console.error('Failed to parse message:', error, event.data);
                }
            }
        } catch (error) {
            console.error('Error handling message:', error);
        }
    }

    private processEvent(event: any) {
        try {
            const eventType = event.type;

            switch (eventType) {
                case 'user':
                    // User transcription from Gemini
                    this.emit('user_transcription', event.text);
                    break;

                case 'gemini':
                    // Gemini's text response
                    this.emit('gemini_response', event.text);
                    break;

                case 'turn_complete':
                    console.log('[GeminiLiveClient] Turn complete event received');
                    this.emit('turn_complete');
                    break;

                case 'interrupted':
                    console.log('[GeminiLiveClient] Interrupted event received');
                    this.emit('interrupted');
                    break;

                case 'openai_transcription':
                    // OpenAI Whisper transcription/translation
                    this.emit('transcription', event.data);
                    break;

                case 'error':
                case 'system_error':
                    console.error('[GeminiLiveClient] Error event:', event.error || event.message);
                    this.emit('error', event.error || event.message);
                    break;

                default:
                    console.debug('[GeminiLiveClient] Unknown event type:', eventType);
            }
        } catch (error) {
            console.error('Error processing event:', error);
        }
    }

    sendAudio(audioData: Uint8Array) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(audioData);
        }
    }

    sendContext(context: any) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'context',
                data: context
            }));
        }
    }

    /**
     * Send structured editor context payload to backend
     */
    sendEditorContext(payload: any) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(payload));
            console.log('Sent editor context:', payload.data?.fileName);
        }
    }

    /**
     * Send selection-only context to backend (new format)
     */
    sendSelectionContext(payload: any) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(payload));
            console.log('Sent selection context:', payload.data?.fileName, `(${payload.data?.selection?.text?.length || 0} chars)`);
        }
    }

    /**
     * Send workspace tree context to backend (new format)
     */
    sendTreeContext(payload: any) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(payload));
            console.log('Sent workspace tree:', payload.data?.roots?.length, 'roots');
        }
    }

    sendText(text: string) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(text);
        }
    }

    private attemptReconnect(url: string) {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Reconnecting... attempt ${this.reconnectAttempts}`);

            setTimeout(() => {
                this.connect(url).catch((error) => {
                    console.error('Reconnect failed:', error);
                });
            }, this.reconnectDelay * this.reconnectAttempts);
        } else {
            this.emit('error', 'Max reconnection attempts reached');
        }
    }

    async disconnect(): Promise<void> {
        if (this.ws) {
            this.reconnectAttempts = this.maxReconnectAttempts; // Prevent reconnect
            this.ws.close();
            this.ws = null;
        }
    }

    isConnected(): boolean {
        return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
    }
}