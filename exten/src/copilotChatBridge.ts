/**
 * Copilot Chat Bridge - Sends prompts to GitHub Copilot Chat.
 */

import * as vscode from 'vscode';

/**
 * Send a prompt to GitHub Copilot Chat.
 */
export async function sendToCopilotChat(prompt: string): Promise<boolean> {
    try {
        await vscode.commands.executeCommand(
            'workbench.action.chat.open',
            { query: prompt, isPartialQuery: false }
        );
        console.log('[CopilotChat] Prompt sent');
        return true;
    } catch (error) {
        console.error('[CopilotChat] Error:', error);
        
        // Check if Copilot is installed
        const copilotExtension = vscode.extensions.getExtension('GitHub.copilot-chat');
        if (!copilotExtension) {
            vscode.window.showErrorMessage(
                'GitHub Copilot Chat is not installed.',
                'Install'
            ).then(action => {
                if (action === 'Install') {
                    vscode.commands.executeCommand(
                        'workbench.extensions.installExtension',
                        'GitHub.copilot-chat'
                    );
                }
            });
        } else {
            vscode.window.showErrorMessage(`Failed to open Copilot Chat: ${error}`);
        }
        
        return false;
    }
}
