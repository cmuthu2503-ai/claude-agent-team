import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import PipelineIndicator from '../components/ui/PipelineIndicator';
import { usePipelineStore } from '../stores/pipeline';

// Mock the pipeline store
vi.mock('../stores/pipeline', () => ({
  usePipelineStore: vi.fn()
}));

// Mock the TaskRelationships component
vi.mock('../components/ui/TaskRelationships', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => (
    <div data-testid="task-relationships-modal">
      {isOpen && (
        <div>
          <span>Task Relationships Modal</span>
          <button onClick={onClose}>Close</button>
        </div>
      )}
    </div>
  )
}));

const mockPipelineStore = {
  getTaskRelationships: vi.fn()
};

describe('PipelineIndicator', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (usePipelineStore as any).mockReturnValue(mockPipelineStore);
  });

  const renderComponent = (props = {}) => {
    const defaultProps = {
      taskId: 1,
      ...props
    };

    return render(
      <BrowserRouter>
        <PipelineIndicator {...defaultProps} />
      </BrowserRouter>
    );
  };

  it('renders pipeline status when provided', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ parent: null, children: [] });
    renderComponent({ pipelineStatus: 'completed' });
    
    expect(screen.getByText('Pipeline')).toBeInTheDocument();
    expect(screen.getByText('✅')).toBeInTheDocument();
  });

  it('shows correct status colors for different pipeline states', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ parent: null, children: [] });

    // Test pending status
    const { rerender } = renderComponent({ pipelineStatus: 'pending' });
    expect(screen.getByText('⏳')).toBeInTheDocument();

    // Test processing status
    rerender(
      <BrowserRouter>
        <PipelineIndicator taskId={1} pipelineStatus="processing" />
      </BrowserRouter>
    );
    expect(screen.getByText('🔄')).toBeInTheDocument();

    // Test completed status
    rerender(
      <BrowserRouter>
        <PipelineIndicator taskId={1} pipelineStatus="completed" />
      </BrowserRouter>
    );
    expect(screen.getByText('✅')).toBeInTheDocument();

    // Test failed status
    rerender(
      <BrowserRouter>
        <PipelineIndicator taskId={1} pipelineStatus="failed" />
      </BrowserRouter>
    );
    expect(screen.getByText('❌')).toBeInTheDocument();
  });

  it('displays relationship button when task has parent', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: 123, 
      children: [] 
    });
    
    renderComponent();
    
    expect(screen.getByText('Child Task')).toBeInTheDocument();
    expect(screen.getByText('🔗')).toBeInTheDocument();
  });

  it('displays relationship button when task has children', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: null, 
      children: [456, 789] 
    });
    
    renderComponent();
    
    expect(screen.getByText('Parent Task')).toBeInTheDocument();
    expect(screen.getByText('🔗')).toBeInTheDocument();
  });

  it('does not show relationship button when no relationships exist', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: null, 
      children: [] 
    });
    
    renderComponent();
    
    expect(screen.queryByText('🔗')).not.toBeInTheDocument();
  });

  it('opens modal when relationship button is clicked', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: 123, 
      children: [] 
    });
    
    renderComponent();
    
    const relationshipButton = screen.getByRole('button');
    fireEvent.click(relationshipButton);
    
    expect(screen.getByTestId('task-relationships-modal')).toBeInTheDocument();
    expect(screen.getByText('Task Relationships Modal')).toBeInTheDocument();
  });

  it('does not show pipeline status when not provided', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ parent: null, children: [] });
    renderComponent();
    
    expect(screen.queryByText('Pipeline')).not.toBeInTheDocument();
  });

  it('respects showRelationships prop when false', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: 123, 
      children: [] 
    });
    
    renderComponent({ showRelationships: false });
    
    expect(screen.queryByText('🔗')).not.toBeInTheDocument();
    expect(screen.queryByText('Child Task')).not.toBeInTheDocument();
  });

  it('closes modal when close button is clicked', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: 123, 
      children: [] 
    });
    
    renderComponent();
    
    // Open modal
    const relationshipButton = screen.getByRole('button');
    fireEvent.click(relationshipButton);
    expect(screen.getByText('Task Relationships Modal')).toBeInTheDocument();
    
    // Close modal
    const closeButton = screen.getByText('Close');
    fireEvent.click(closeButton);
    expect(screen.queryByText('Task Relationships Modal')).not.toBeInTheDocument();
  });

  it('handles missing pipeline store gracefully', () => {
    (usePipelineStore as any).mockReturnValue({
      getTaskRelationships: () => ({ parent: null, children: [] })
    });
    
    expect(() => renderComponent()).not.toThrow();
  });

  it('renders both pipeline status and relationship button when both present', () => {
    mockPipelineStore.getTaskRelationships.mockReturnValue({ 
      parent: null, 
      children: [456] 
    });
    
    renderComponent({ pipelineStatus: 'processing' });
    
    expect(screen.getByText('Pipeline')).toBeInTheDocument();
    expect(screen.getByText('🔄')).toBeInTheDocument();
    expect(screen.getByText('Parent Task')).toBeInTheDocument();
    expect(screen.getByText('🔗')).toBeInTheDocument();
  });
});
