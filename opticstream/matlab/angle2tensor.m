
function moving_OC = angle2tensor (moving_o1)

% convert angle in degrees to orientation tensor 
if length(size(moving_o1)) == 2
    moving_OC=zeros([size(moving_o1) 4]);
    moving_OC(:,:,1)=cos(moving_o1/180*pi).*cos(moving_o1/180*pi);
    moving_OC(:,:,2)=cos(moving_o1/180*pi).*sin(moving_o1/180*pi);
    moving_OC(:,:,3)=moving_OC(:,:,2);
    moving_OC(:,:,4)=sin(moving_o1/180*pi).*sin(moving_o1/180*pi);
else
     moving_OC=zeros([size(moving_o1) 4]);
     for zz = 1:size (moving_o1,3)
         moving_OC(:,:,zz,1)=cos(moving_o1(:,:,zz)/180*pi).*cos(moving_o1(:,:,zz)/180*pi);
         moving_OC(:,:,zz,2)=cos(moving_o1(:,:,zz)/180*pi).*sin(moving_o1(:,:,zz)/180*pi);
         moving_OC(:,:,zz,3)=moving_OC(:,:,zz,2);
         moving_OC(:,:,zz,4)=sin(moving_o1(:,:,zz)/180*pi).*sin(moving_o1(:,:,zz)/180*pi);
     end
end