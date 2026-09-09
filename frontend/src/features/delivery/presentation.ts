export function deliveryMessage(status: string) {
  switch (status) {
    case 'reviewed': return 'Reviewed. Not sent.'
    case 'sending': return 'Sending. Receipt pending.'
    case 'uncertain': return 'Delivery result is uncertain. Reconcile the receipt before sending again.'
    case 'reconciling': return 'Checking the remote receipt…'
    case 'delivered': return 'Finding text and evidence manifest are delivered.'
    case 'rejected': return 'The previous review is no longer valid. Preview the current revision again.'
    default: return 'No confirmed delivery receipt.'
  }
}
export function attachmentMessage(status: string) {
  return status === 'delivered' ? 'Selected evidence files must be attached in Ghostwriter; they were not uploaded.' : 'Evidence files will need to be attached in Ghostwriter. They have not been uploaded.'
}
